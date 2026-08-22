"""C13: option-conditioned graph retrieval with rebuttal-first evidence checks."""

from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from analyze_dense_retrieval import embed
from c8_graph_passage import (
    C8Context,
    HIGH_VALUE_RELATIONS,
    LETTERS,
    LOW_VALUE_RELATIONS,
    normalize_letter,
    personalized_graph_scores,
    question_type,
    retrieve_bm25,
)
from novel_kg_studio.schema import norm_text

VERSION = "c13-option-conditioned-graph-rebuttal-v2"


def _diverse(order: list[int], limit: int) -> list[int]:
    selected: list[int] = []
    for index in order:
        if any(abs(index - old) <= 1 for old in selected):
            continue
        selected.append(index)
        if len(selected) >= limit:
            break
    return selected


def _option_packet(
    ctx: C8Context,
    matrix: np.ndarray,
    query_vector: np.ndarray,
    q: dict[str, Any],
    option_index: int,
) -> dict[str, Any]:
    option = q["choices"][option_index]
    local_q = {"question": q["question"], "choices": [option]}
    bm25 = [int(x) for x in retrieve_bm25(ctx, local_q, limit=36)["diagnostics"]["selected"]]
    dense_scores = matrix @ query_vector
    dense = list(map(int, np.argsort(dense_scores)[::-1][:80]))

    node_scores = personalized_graph_scores(ctx, q["question"], [option])
    by_id = {node["id"]: index for index, node in enumerate(ctx.base.store.nodes)}
    chunk_mass: dict[int, float] = defaultdict(float)
    edge_rows: list[tuple[float, int, int]] = []
    for edge_index, edge in enumerate(ctx.base.store.edges):
        source = by_id.get(edge.get("source")); target = by_id.get(edge.get("target"))
        if source is None or target is None:
            continue
        relation = str(edge.get("type", ""))
        relation_weight = 1.8 if relation in HIGH_VALUE_RELATIONS else 0.2 if relation in LOW_VALUE_RELATIONS else 0.8
        mass = relation_weight * float(edge.get("confidence") or 0.5) * (float(node_scores[source]) + float(node_scores[target]))
        for chunk_id in ctx.edge_to_chunks.get(edge_index, set()):
            chunk_mass[int(chunk_id)] += mass
            edge_rows.append((mass, edge_index, int(chunk_id)))
    graph = [index for index, _ in sorted(chunk_mass.items(), key=lambda item: item[1], reverse=True)]

    bm_rank = {index: rank for rank, index in enumerate(bm25)}
    dense_rank = {index: rank for rank, index in enumerate(dense)}
    graph_rank = {index: rank for rank, index in enumerate(graph)}
    candidates = set(bm25[:36]) | set(dense[:80]) | set(graph[:40])
    rrf = {
        index: (
            1.0 / (40 + bm_rank.get(index, 10**6))
            + 1.2 / (40 + dense_rank.get(index, 10**6))
            + 1.5 / (40 + graph_rank.get(index, 10**6))
        )
        for index in candidates
    }
    selected = _diverse(sorted(candidates, key=lambda index: rrf[index], reverse=True), 5)

    links = []
    seen_edges: set[int] = set()
    selected_set = set(selected)
    for mass, edge_index, chunk_id in sorted(edge_rows, reverse=True):
        if chunk_id not in selected_set or edge_index in seen_edges:
            continue
        edge = ctx.base.store.edges[edge_index]
        if str(edge.get("type", "")) not in HIGH_VALUE_RELATIONS:
            continue
        source = ctx.base.store.by_id.get(edge.get("source"), {})
        target = ctx.base.store.by_id.get(edge.get("target"), {})
        links.append(
            {
                "source": source.get("name", ""),
                "relation": edge.get("type", ""),
                "target": target.get("name", ""),
                "chunk_id": ctx.base.chunks[chunk_id].id,
                "evidence": str(edge.get("evidence", "")),
                "score": mass,
            }
        )
        seen_edges.add(edge_index)
        if len(links) >= 3:
            break
    chunks = [
        {
            "id": ctx.base.chunks[index].id,
            "index": index,
            "start": ctx.base.chunks[index].start,
            "end": ctx.base.chunks[index].end,
            "text": ctx.base.chunks[index].text,
            "rrf_score": rrf[index],
        }
        for index in selected
    ]
    return {"letter": LETTERS[option_index], "option": option, "chunks": chunks, "links": links}


def _grounded_quote(quote: Any, source: str) -> str:
    raw = str(quote or "").strip().strip('"\'')
    target = norm_text(raw)
    if len(target) < 18 or target not in norm_text(source):
        return ""
    return raw


def _check_option(client: Any, q: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    passages = "\n\n".join(f"[{row['id']}]\n{row['text']}" for row in packet["chunks"])
    paths = "\n".join(
        f"- {row['source']} --{row['relation']}--> {row['target']} [{row['chunk_id']}]"
        for row in packet["links"]
    )
    prompt = (
        f"QUESTION\n{q['question']}\n\nOPTION CLAIM\n{packet['letter']}. {packet['option']}\n\n"
        f"OPTION-CONDITIONED SOURCE PASSAGES\n{passages}"
        + (f"\n\nGRAPH PATH HINTS (navigation hints only; verify in passages)\n{paths}" if paths else "")
        + "\n\nTreat the option as a claim that directly answers the question. Find the strongest exact source quote "
        "SUPPORTING it and the strongest exact source quote CONTRADICTING it. A contradiction must explicitly give "
        "a different identity, number, place, time, action, motive, or say the claim is false. The machine translation often "
        "changes character names: a quote can still support the claim when the described event and answer value match, even "
        "if the question's alias is absent. Missing evidence is UNKNOWN, "
        "never contradiction. For a NOT/EXCEPT/incorrect question, support means the option really satisfies that negative "
        "condition. Resolve obvious translated aliases, but copy quotes verbatim. Re-check numbers and names against the "
        "option text. Return strict JSON only: "
        '{"support_quote":"verbatim or empty","contradiction_quote":"verbatim or empty","analysis":"short"}'
    )
    raw = client.complete_json(
        "You are an evidence auditor. You do not choose among options and you never equate absence with contradiction.",
        prompt,
        max_tokens=420,
    )
    support = _grounded_quote(raw.get("support_quote", "") if isinstance(raw, dict) else "", passages)
    contradiction = _grounded_quote(raw.get("contradiction_quote", "") if isinstance(raw, dict) else "", passages)
    return {
        "letter": packet["letter"],
        "option": packet["option"],
        "support_quote": support,
        "contradiction_quote": contradiction,
        "analysis": str(raw.get("analysis", "")) if isinstance(raw, dict) else "",
        "status": "mixed" if support and contradiction else "supported" if support else "contradicted" if contradiction else "unknown",
        "chunk_ids": [row["id"] for row in packet["chunks"]],
        "links": packet["links"],
        "raw": raw,
    }


def run_c13(
    client: Any,
    q: dict[str, Any],
    graph: dict[str, Any],
    novel_text: str,
    chunk_matrix: np.ndarray,
    option_workers: int = 2,
) -> dict[str, Any]:
    started = time.time()
    ctx = C8Context.build(graph, novel_text, None)
    queries = [f"Question: {q['question']}\nCandidate answer: {choice}" for choice in q["choices"][:4]]
    query_matrix = embed(queries)
    packets = [_option_packet(ctx, chunk_matrix, query_matrix[index], q, index) for index in range(4)]
    with ThreadPoolExecutor(max_workers=option_workers) as pool:
        checks = list(pool.map(lambda packet: _check_option(client, q, packet), packets))

    options = "\n".join(f"{LETTERS[index]}. {choice}" for index, choice in enumerate(q["choices"][:4]))
    evidence_rows = []
    for row in checks:
        evidence_rows.append(
            f"{row['letter']}. {row['option']}\n"
            f"  support: {row['support_quote'] or '[none]'}\n"
            f"  contradiction: {row['contradiction_quote'] or '[none]'}\n"
            f"  auditor note: {row['analysis'][:600] or '[none]'}"
        )
    prompt = (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options}\n\nVERIFIED OPTION EVIDENCE\n"
        + "\n\n".join(evidence_rows)
        + "\n\nChoose the option best supported by the quoted novel text. Independently compare every quote with the exact "
        "option text: an auditor label may be wrong. Eliminate an option only when its contradiction quote truly states "
        "a different answer to this question. No quote means unknown, not false. For NOT/EXCEPT/incorrect questions, "
        "choose the option satisfying the requested negative condition. Pay special attention to exact names and numbers. "
        "Return strict JSON only: "
        '{"selected_letter":"A|B|C|D","reason":"brief evidence comparison"}'
    )
    raw = client.complete_json(
        "You are a conservative multiple-choice judge using only verified quotes from a novel.",
        prompt,
        max_tokens=420,
    )
    selected = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw)
    return {
        "method": "c13_option_conditioned_graph_rebuttal",
        "selected_letter": selected,
        "selected_text": q["choices"][LETTERS.index(selected)] if selected in LETTERS else "",
        "reason": str(raw.get("reason", "")) if isinstance(raw, dict) else "",
        "checks": checks,
        "raw": raw,
        "question_type": question_type(q["question"]),
        "elapsed_seconds": round(time.time() - started, 3),
        "prompt_version": VERSION,
    }
