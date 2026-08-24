"""Offline gold-recall audit for G7 graph retrieval variants.

Gold annotations are used only after retrieval to score frozen, question-time
policies.  They are never supplied to a retriever or used to select passages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from analyze_dense_retrieval import chunk_embeddings, embed  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c13_option_rebuttal import _option_packet  # noqa: E402
from c8_graph_passage import C8Context, personalized_graph_scores, retrieve_bm25  # noqa: E402

DATA = ROOT.parent / "datasets" / "external" / "detectiveqa"
DEFAULT_GRAPH_ROOT = ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "batch03"
DEFAULT_NOVELS = ["93", "97", "99", "100", "103", "104", "105", "106", "107", "108"]
PARA_RE = re.compile(r"(?m)^\[(\d+)\]\s*")


def annotation(novel: str, qid: str) -> dict[str, Any]:
    source = "human_anno" if "_human_anno_" in qid else "AIsup_anno"
    qi = int(qid.rsplit("_", 1)[-1])
    data = json.loads((DATA / "anno_data_en" / source / f"{novel}.json").read_text(encoding="utf-8"))
    record = data[0] if isinstance(data, list) else data
    return record["questions"][qi]


def paragraph_spans(text: str) -> dict[int, tuple[int, int]]:
    matches = list(PARA_RE.finditer(text))
    return {
        int(match.group(1)): (match.start(), matches[index + 1].start() if index + 1 < len(matches) else len(text))
        for index, match in enumerate(matches)
    }


def gold_chunk_ids(ctx: C8Context, positions: set[int], spans: dict[int, tuple[int, int]]) -> set[int]:
    result = set()
    for index, chunk in enumerate(ctx.base.chunks):
        for position in positions:
            span = spans.get(position)
            if span and chunk.start < span[1] and chunk.end > span[0]:
                result.add(index)
                break
    return result


def ordered_unique(values):
    seen = set()
    return [value for value in values if not (value in seen or seen.add(value))]


def candidate_rows(ctx: C8Context, matrix: np.ndarray, vectors: np.ndarray, question: dict[str, Any]):
    packets = [_option_packet(ctx, matrix, vectors[index], question, index) for index in range(4)]
    rows = []
    for option_index, packet in enumerate(packets):
        for rank, chunk in enumerate(packet["chunks"]):
            rows.append((float(chunk.get("rrf_score") or 0.0), option_index, rank, int(chunk["index"])))
    return packets, rows


def g7(rows, limit: int = 8):
    selected = []
    for option_index in range(4):
        candidates = sorted((row for row in rows if row[1] == option_index), key=lambda row: (-row[0], row[2]))
        for row in candidates:
            if row[3] not in selected:
                selected.append(row[3])
                break
    for row in sorted(rows, key=lambda row: (-row[0], row[2])):
        if row[3] not in selected:
            selected.append(row[3])
        if len(selected) >= limit:
            break
    return selected[:limit]


def expand_neighbors(selected: list[int], total: int, radius: int = 1, limit: int = 12):
    result = list(selected)
    for index in selected:
        for offset in range(1, radius + 1):
            for neighbor in (index - offset, index + offset):
                if 0 <= neighbor < total and neighbor not in result:
                    result.append(neighbor)
                    if len(result) >= limit:
                        return result
    return result


def edge_evidence_expansion(ctx: C8Context, question: dict[str, Any], selected: list[int], limit: int):
    scores = personalized_graph_scores(ctx, question["question"], question["choices"][:4])
    by_id = {node["id"]: index for index, node in enumerate(ctx.base.store.nodes)}
    mass = defaultdict(float)
    for edge_index, edge in enumerate(ctx.base.store.edges):
        source = by_id.get(edge.get("source")); target = by_id.get(edge.get("target"))
        if source is None or target is None:
            continue
        value = float(edge.get("confidence") or 0.5) * (float(scores[source]) + float(scores[target]))
        for chunk_id in ctx.edge_to_chunks.get(edge_index, set()):
            mass[int(chunk_id)] += value
    result = list(selected)
    for chunk_id, _ in sorted(mass.items(), key=lambda item: item[1], reverse=True):
        if chunk_id not in result:
            result.append(chunk_id)
            if len(result) >= limit:
                break
    return result


def metrics(rows, key):
    return {
        "questions": len(rows),
        "any_gold": sum(bool(row[key] & row["gold_chunks"]) for row in rows) / len(rows),
        "answer_gold": sum(bool(row[key] & row["answer_chunks"]) for row in rows) / len(rows),
        "mean_chunks": sum(len(row[key]) for row in rows) / len(rows),
        "mean_chars": sum(sum(row["chunk_chars"][index] for index in row[key]) for row in rows) / len(rows),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=DEFAULT_NOVELS)
    parser.add_argument("--graph-root", type=Path, default=DEFAULT_GRAPH_ROOT)
    parser.add_argument("--out", type=Path, default=ROOT / "reports" / "DQA30_GRAPH_RECALL_AUDIT_20260824.json")
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    rows = []
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads((args.graph_root / "novels" / novel / "graph.json").read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        queries = [
            f"Question: {q['question']}\nCandidate answer: {choice}"
            for q in case["questions"] for choice in q["choices"][:4]
        ]
        query_matrix = embed(queries)
        spans = paragraph_spans(case["text"])
        for qi, question in enumerate(case["questions"]):
            anno = annotation(novel, question["qid"])
            clue_positions = {int(value) for value in anno.get("clue_position") or [] if int(value) >= 0}
            answer_position = int(anno.get("answer_position") or -1)
            all_positions = clue_positions | ({answer_position} if answer_position >= 0 else set())
            gold_chunks = gold_chunk_ids(ctx, all_positions, spans)
            answer_chunks = gold_chunk_ids(ctx, {answer_position}, spans) if answer_position >= 0 else set()
            _, candidates = candidate_rows(ctx, matrix, query_matrix[qi * 4:qi * 4 + 4], question)
            base8 = g7(candidates, 8)
            base12 = g7(candidates, 12)
            base16 = g7(candidates, 16)
            bm25 = [int(value) for value in retrieve_bm25(ctx, question, limit=36)["diagnostics"]["selected"]]
            variants = {
                "g7_8": base8,
                "g7_12": base12,
                "g7_16": base16,
                "g7_8_neighbor12": expand_neighbors(base8, len(ctx.base.chunks), 1, 12),
                "g7_8_edge12": edge_evidence_expansion(ctx, question, base8, 12),
                "g7_8_neighbor_edge16": edge_evidence_expansion(
                    ctx, question, expand_neighbors(base8, len(ctx.base.chunks), 1, 12), 16
                ),
                "g7_8_bm25_12": ordered_unique(base8 + bm25)[:12],
                "g7_8_bm25_neighbor16": expand_neighbors(ordered_unique(base8 + bm25)[:12], len(ctx.base.chunks), 1, 16),
                "candidate_union": sorted({row[3] for row in candidates}),
                "bm25_8": bm25[:8],
                "bm25_12": bm25[:12],
                "bm25_16": bm25[:16],
            }
            row = {
                "novel": novel, "qi": qi, "qid": question["qid"],
                "gold_positions": sorted(all_positions), "answer_position": answer_position,
                "gold_chunks": gold_chunks, "answer_chunks": answer_chunks,
                "chunk_chars": [len(chunk.text) for chunk in ctx.base.chunks],
                **{key: set(value) for key, value in variants.items()},
            }
            rows.append(row)
        print(f"audited novel {novel}", flush=True)
    keys = [key for key in rows[0] if key.startswith(("g7_", "bm25_", "candidate_"))]
    report = {
        "guard": "Gold is evaluation-only and is never passed to retrieval.",
        "novels": args.novels,
        "variants": {key: metrics(rows, key) for key in keys},
        "per_novel": {
            novel: {key: metrics([row for row in rows if row["novel"] == novel], key) for key in keys}
            for novel in args.novels
        },
        "misses": [
            {"novel": row["novel"], "qi": row["qi"], "qid": row["qid"], "gold_positions": row["gold_positions"]}
            for row in rows if not (row["g7_8"] & row["gold_chunks"])
        ],
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["variants"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
