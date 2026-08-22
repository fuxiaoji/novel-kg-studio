"""C10: independently verify each option in a small context, then arbitrate."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np

from c8_graph_passage import (
    C8Context, HIGH_VALUE_RELATIONS, LETTERS, LOW_VALUE_RELATIONS, normalize_letter,
    personalized_graph_scores, question_type, retrieve_bm25,
)
from novel_kg_studio.schema import norm_text

VERSION = "c10-independent-option-graph-expand-v2"


def _option_package(ctx: C8Context, q: dict[str, Any], option: str) -> dict[str, Any]:
    local_q = {"question": q["question"], "choices": [option]}
    global_seed = retrieve_bm25(ctx, q, limit=12)
    selected = list(global_seed["diagnostics"]["selected"])
    specific_seed = retrieve_bm25(ctx, local_q, limit=8)
    for raw in specific_seed["diagnostics"]["selected"]:
        index = int(raw)
        if index in selected or any(abs(index - old) <= 1 for old in selected):
            continue
        selected.append(index)
        if len(selected) >= 14:
            break
    # The full option set often supplies aliases that repair translated query
    # terms (for example, inheritance -> will); BM25 remains claim-specific.
    rank = personalized_graph_scores(ctx, q["question"], q["choices"][:4])
    by_id = {node["id"]: index for index, node in enumerate(ctx.base.store.nodes)}
    chunk_mass: dict[int, float] = defaultdict(float)
    edge_rows = []
    for edge_index, edge in enumerate(ctx.base.store.edges):
        source = by_id.get(edge.get("source")); target = by_id.get(edge.get("target"))
        if source is None or target is None:
            continue
        relation = str(edge.get("type", ""))
        weight = 1.4 if relation in HIGH_VALUE_RELATIONS else 0.3 if relation in LOW_VALUE_RELATIONS else 1.0
        mass = weight * float(edge.get("confidence") or 0.6) * (rank[source] + rank[target])
        for chunk_id in ctx.edge_to_chunks.get(edge_index, set()):
            chunk_mass[int(chunk_id)] += mass
            edge_rows.append((mass, edge_index, int(chunk_id)))
    for chunk_id, _ in sorted(chunk_mass.items(), key=lambda item: item[1], reverse=True):
        if chunk_id in selected or any(abs(chunk_id - old) <= 1 for old in selected):
            continue
        selected.append(chunk_id)
        if len(selected) >= 16:
            break
    selected_set = set(selected)
    links = []
    seen = set()
    for mass, edge_index, chunk_id in sorted(edge_rows, reverse=True):
        if chunk_id not in selected_set or edge_index in seen:
            continue
        edge = ctx.base.store.edges[edge_index]
        if str(edge.get("type", "")) not in HIGH_VALUE_RELATIONS:
            continue
        source = ctx.base.store.by_id.get(edge.get("source"), {}); target = ctx.base.store.by_id.get(edge.get("target"), {})
        evidence = str(edge.get("evidence", "")).strip()
        links.append({"source": source.get("name", ""), "relation": edge.get("type", ""), "target": target.get("name", ""), "chunk_id": ctx.base.chunks[chunk_id].id, "evidence": evidence, "score": mass})
        seen.add(edge_index)
        if len(links) >= 4:
            break
    chunks = []
    for index in sorted(selected):
        chunk = ctx.base.chunks[index]
        chunks.append({"id": chunk.id, "start": chunk.start, "end": chunk.end, "text": chunk.text})
    return {"chunks": chunks, "links": links, "diagnostics": {"selected": selected}}


def _grounded_quote(quote: str, passages: str) -> bool:
    target = norm_text(str(quote or "").strip(' "\''))
    source = norm_text(passages)
    if len(target) < 24:
        return False
    return target in source


def _verdict(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("contradict", "false", "refute", "incorrect")):
        return "contradicted"
    if any(token in text for token in ("support", "true", "entail", "correct")):
        return "supported"
    return "unknown"


def _verify_one(client: Any, q: dict[str, Any], option_index: int, ctx: C8Context) -> dict[str, Any]:
    option = q["choices"][option_index]
    package = _option_package(ctx, q, option)
    links = package.get("links", [])
    passages = "\n\n".join(f"[{chunk['id']}]\n{chunk['text']}" for chunk in package["chunks"])
    link_text = "\n".join(
        f"- {link['source']} --{link['relation']}--> {link['target']} (verify in {link['chunk_id']})"
        for link in links[:4]
    )
    prompt = (
        f"QUESTION\n{q['question']}\n\nCLAIM TO CHECK\n{LETTERS[option_index]}. {option}\n\n"
        f"SOURCE PASSAGES\n{passages}"
        + (f"\n\nGRAPH INDEX HINTS (may be noisy)\n{link_text}" if link_text else "")
        + "\n\nJudge only this claim. The question may be machine-translated: resolve obvious aliases and semantic "
        "paraphrases (for example estate/inheritance/will), but do not invent facts. Missing evidence means unknown, not false. "
        "Scan every passage. Treat an explicit statement of the same event as support even when wording differs; "
        "do not demand that the passage repeat the question. If any passage supports or refutes the claim, you must "
        "copy a short verbatim quote. Use unknown only after finding no relevant statement. Return strict JSON only: "
        '{"verdict":"supported|contradicted|unknown","quote":"verbatim source words or empty","reason":"short"}'
    )
    raw = client.complete_json(
        "You verify one claim against source text. Do not compare it with unseen answer options.",
        prompt,
        max_tokens=350,
    )
    verdict = _verdict(raw.get("verdict") if isinstance(raw, dict) else raw)
    quote = str(raw.get("quote", "")) if isinstance(raw, dict) else ""
    grounded = _grounded_quote(quote, passages)
    if verdict != "unknown" and not grounded:
        verdict = "unknown"
    return {
        "letter": LETTERS[option_index], "option": option, "verdict": verdict,
        "quote": quote if grounded else "", "quote_grounded": grounded,
        "reason": str(raw.get("reason", "")) if isinstance(raw, dict) else "",
        "chunk_ids": [chunk["id"] for chunk in package["chunks"]], "links": links[:4], "raw": raw,
    }


def run_c10(client: Any, q: dict[str, Any], graph: dict[str, Any], novel_text: str, option_workers: int = 3) -> dict[str, Any]:
    started = time.time()
    ctx = C8Context.build(graph, novel_text, None)
    with ThreadPoolExecutor(max_workers=option_workers) as pool:
        checks = list(pool.map(lambda index: _verify_one(client, q, index, ctx), range(4)))
    options = "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(q["choices"][:4]))
    audit = "\n".join(
        f"{row['letter']}: {row['verdict']}; grounded quote: {row['quote'] or '[none]'}"
        for row in checks
    )
    prompt = (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options}\n\nINDEPENDENT CLAIM CHECKS\n{audit}\n\n"
        "Select the best answer from these independent checks. For an ordinary question, never select a contradicted "
        "claim: prefer grounded support, and if none exists solve conservatively from the question and options. "
        "Only for an explicit EXCEPT/incorrect/false question choose the contradicted claim. "
        "If evidence remains tied, make the least-assumptive choice. Return strict JSON only: "
        '{"selected_letter":"A|B|C|D","reason":"one short sentence"}'
    )
    raw = client.complete_json(
        "You arbitrate short, independently verified claims. Do not invent evidence.", prompt, max_tokens=350
    )
    letter = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw)
    return {
        "method": "c10_independent_option_verifier", "selected_letter": letter,
        "selected_text": q["choices"][LETTERS.index(letter)] if letter in LETTERS else "",
        "reason": str(raw.get("reason", "")) if isinstance(raw, dict) else "",
        "checks": checks, "raw": raw, "question_type": question_type(q["question"]),
        "elapsed_seconds": round(time.time() - started, 3), "prompt_version": VERSION,
    }
