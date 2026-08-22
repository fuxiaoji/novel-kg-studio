"""C9: equal-budget tail plus graph-indexed whole-book evidence fusion."""

from __future__ import annotations

import time
from typing import Any

from c8_graph_passage import C8Context, LETTERS, answer_prompt, normalize_letter, question_type, retrieve_bm25, retrieve_graph

VERSION = "c9-tail24-graph10-equal34-v1"
TOTAL_CHUNKS = 34
TAIL_CHUNKS = 24


def _chunk_row(ctx: C8Context, index: int, source: str) -> dict[str, Any]:
    chunk = ctx.base.chunks[index]
    return {"id": chunk.id, "start": chunk.start, "end": chunk.end, "text": chunk.text, "score": 0.0, "source": source}


def retrieve_tail50(ctx: C8Context, q: dict[str, Any]) -> dict[str, Any]:
    del q
    selected = list(range(max(0, len(ctx.base.chunks) - TOTAL_CHUNKS), len(ctx.base.chunks)))
    return {
        "chunks": [_chunk_row(ctx, index, "matched_tail") for index in selected],
        "links": [],
        "novel_length": len(ctx.base.novel_text),
        "diagnostics": {"selected": selected, "tail_chunks": len(selected), "global_chunks": 0},
    }


def retrieve_hybrid(ctx: C8Context, q: dict[str, Any]) -> dict[str, Any]:
    tail = list(range(max(0, len(ctx.base.chunks) - TAIL_CHUNKS), len(ctx.base.chunks)))
    selected = list(tail)
    bm25 = retrieve_bm25(ctx, q, limit=24)
    for raw in bm25["diagnostics"]["selected"]:
        index = int(raw)
        if index in selected or any(abs(index - old) <= 1 for old in selected):
            continue
        selected.append(index)
        if len(selected) >= TOTAL_CHUNKS:
            break
    selected_set = {ctx.base.chunks[index].id for index in selected}
    graph = retrieve_graph(ctx, q, limit=18)
    links = []
    for link in graph.get("links", []):
        evidence = str(link.get("evidence", "")).strip()
        evidence_norm = evidence.lower()
        source = str(link.get("source", "")).strip()
        target = str(link.get("target", "")).strip()
        explicitly_grounded = source.lower() in evidence_norm or target.lower() in evidence_norm
        if link.get("chunk_id") not in selected_set or len(evidence) < 35 or not explicitly_grounded:
            continue
        links.append(link)
        if len(links) >= 5:
            break
    selected.sort()
    return {
        "chunks": [_chunk_row(ctx, index, "tail" if index in tail else "whole_book_graph_seed") for index in selected],
        "links": links,
        "novel_length": len(ctx.base.novel_text),
        "diagnostics": {
            "selected": selected,
            "tail_chunks": sum(index in tail for index in selected),
            "global_chunks": sum(index not in tail for index in selected),
        },
    }


def run_c9(method: str, client: Any, q: dict[str, Any], graph: dict[str, Any], novel_text: str) -> dict[str, Any]:
    started = time.time()
    ctx = C8Context.build(graph, novel_text, None)
    package = retrieve_tail50(ctx, q) if method == "tail50" else retrieve_hybrid(ctx, q)
    raw = client.complete_json(
        "You are a careful small-context detective-novel reader. Use only supplied passages and return one answer.",
        answer_prompt(q, package),
        max_tokens=500,
    )
    letter = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw)
    return {
        "method": f"c9_{method}",
        "selected_letter": letter,
        "selected_text": q["choices"][LETTERS.index(letter)] if letter in LETTERS else "",
        "reason": str(raw.get("reason", "")) if isinstance(raw, dict) else "",
        "retrieval": package,
        "raw": raw,
        "question_type": question_type(q["question"]),
        "elapsed_seconds": round(time.time() - started, 3),
        "prompt_version": VERSION,
    }
