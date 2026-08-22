"""Offline gold-evidence audit for BGE-M3 dense and dense+BM25 retrieval."""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from analyze_c8_retrieval import QID_RE, annotations, load_closed, overlaps, paragraph_spans, summarize  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c8_graph_passage import C8Context, retrieve_bm25  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, graph_path  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
CACHE = BASE / "dqa_bgem3_retrieval_cache"
MODEL = "bge-m3"


def embed(inputs: list[str]) -> np.ndarray:
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/embed",
        data=json.dumps({"model": MODEL, "input": inputs, "keep_alive": "30m"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        payload = json.loads(response.read().decode("utf-8"))
    matrix = np.asarray(payload["embeddings"], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


def chunk_embeddings(novel: str, texts: list[str]) -> np.ndarray:
    CACHE.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256("\n<chunk>\n".join(texts).encode("utf-8")).hexdigest()
    path = CACHE / f"{novel}.npz"
    if path.exists():
        data = np.load(path, allow_pickle=False)
        if str(data["digest"].item()) == digest and str(data["model"].item()) == MODEL:
            return data["embeddings"]
    blocks = []
    for start in range(0, len(texts), 32):
        blocks.append(embed(texts[start : start + 32]))
        print(f"embed {novel}: {min(start + 32, len(texts))}/{len(texts)}", flush=True)
    matrix = np.vstack(blocks)
    np.savez_compressed(path, embeddings=matrix, digest=np.asarray(digest), model=np.asarray(MODEL))
    return matrix


def diverse(order: np.ndarray, limit: int) -> list[int]:
    selected = []
    for raw in order:
        index = int(raw)
        if any(abs(index - old) <= 1 for old in selected):
            continue
        selected.append(index)
        if len(selected) >= limit:
            break
    return selected


def rank_dense(matrix: np.ndarray, q: dict[str, Any], limit: int = 18) -> tuple[list[int], np.ndarray]:
    queries = [q["question"], *[f"{q['question']}\nCandidate: {choice}" for choice in q["choices"][:4]]]
    qmatrix = embed(queries)
    scores = np.max(matrix @ qmatrix.T, axis=1)
    return diverse(np.argsort(scores)[::-1], limit), scores


def rrf(bm25_ids: list[int], dense_scores: np.ndarray, limit: int = 18) -> list[int]:
    dense_order = list(map(int, np.argsort(dense_scores)[::-1]))
    bm25_rank = {index: rank for rank, index in enumerate(bm25_ids)}
    dense_rank = {index: rank for rank, index in enumerate(dense_order)}
    candidates = set(dense_order[:80]) | set(bm25_ids)
    score = {
        index: 1 / (60 + dense_rank.get(index, 10**6)) + 1 / (60 + bm25_rank.get(index, 10**6))
        for index in candidates
    }
    return diverse(np.asarray(sorted(candidates, key=lambda index: score[index], reverse=True)), limit)


def main() -> None:
    cases = merged_cases(NOVELS)
    qwen = load_closed(BASE / "dqa_qwen_question_only20" / "answers")
    deepseek = load_closed(BASE / "dqa_deepseek_v4flash_full_vs_question20_nothinking" / "answers" / "question_only")
    rows = []
    for novel in NOVELS:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        spans = paragraph_spans(case["text"])
        anno = annotations(novel)
        for qi, q in enumerate(case["questions"]):
            match = QID_RE.match(q["qid"])
            raw = anno[(match.group(2), int(match.group(3)))]
            clues = sorted({int(pos) for pos in raw.get("clue_position", []) if int(pos) >= 0 and int(pos) in spans})
            answer_pos = int(raw.get("answer_position", -1))
            bm25_ids = retrieve_bm25(ctx, q, limit=18)["diagnostics"]["selected"]
            dense_ids, dense_scores = rank_dense(matrix, q)
            packages = {"bm25": bm25_ids, "dense": dense_ids, "rrf": rrf(bm25_ids, dense_scores)}
            row: dict[str, Any] = {"novel": novel, "qi": qi, "qid": q["qid"], "clue_total": len(clues), "qwen_closed": qwen[q["qid"]], "deepseek_closed": deepseek[q["qid"]]}
            for method, ids in packages.items():
                chunks = [{"start": ctx.base.chunks[index].start, "end": ctx.base.chunks[index].end} for index in ids]
                hits = sum(overlaps(chunks, spans.get(pos)) for pos in clues)
                row[f"{method}_clue_hits"] = hits
                row[f"{method}_any_clue"] = hits > 0
                row[f"{method}_answer_hit"] = overlaps(chunks, spans.get(answer_pos))
                row[f"{method}_chars"] = sum(len(ctx.base.chunks[index].text) for index in ids)
                row[f"{method}_ids"] = ids
            rows.append(row)
        print(f"audit {novel}: {len(case['questions'])} questions", flush=True)
    sets = {"all": rows, "first10": [row for row in rows if row["novel"] in FIRST10], "qwen_hard": [row for row in rows if not row["qwen_closed"]], "conservative_hard": [row for row in rows if not row["qwen_closed"] and not row["deepseek_closed"]]}
    report = {name: {method: summarize(subset, method) for method in ("bm25", "dense", "rrf")} for name, subset in sets.items()}
    out = BASE / "dqa_bgem3_retrieval_audit.json"
    out.write_text(json.dumps({"model": MODEL, "report": report, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
