"""Run only the missing Qwen3.5-9B compression and ordinary-RAG baselines on old20.

Existing Q0 and tail predictions are copied from their frozen per-question CSVs.
Graphs are byte-verified against config/dqa30_frozen_graphs.json and are used only
as a convenient source of the novel chunk index for B3; no graph relation enters
the ordinary-RAG prompt.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "scripts").is_dir():
    ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from analyze_dense_retrieval import chunk_embeddings, embed  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c8_graph_passage import C8Context, LETTERS  # noqa: E402
from native_ollama_client import NativeOllamaNoThinkClient  # noqa: E402
from run_compress_20 import compress_novel_parallel  # noqa: E402
from run_dqa30_batch_eval import answer_compressed, answer_evidence  # noqa: E402

VERSION = "dqa30-frozen-old20-missing-baselines-v1"
OLD20 = ["26", "27", "28", "30", "31", "33", "40", "53", "56", "79", "15", "16", "25", "29", "81", "82", "83", "84", "87", "90"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def csv_index(path: Path, key_fields: tuple[str, ...]) -> dict[tuple[str, ...], dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {tuple(row[field] for field in key_fields): row for row in csv.DictReader(handle)}


def valid(path: Path, graph_hash: str) -> bool:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return (
            row.get("version") == VERSION
            and row.get("model") == "qwen3.5:9b"
            and row.get("graph_sha256") == graph_hash
            and all(row.get("answers", {}).get(method, {}).get("selected_letter") in LETTERS for method in ("Q0", "B1", "B2", "B3"))
        )
    except Exception:
        return False


def estimated_chars(question: dict[str, Any], result: dict[str, Any], method: str) -> int:
    base = len(question["question"]) + sum(len(choice) for choice in question["choices"][:4]) + 120
    if method == "B1":
        return base + 50_000
    if method == "B2":
        return base + int(result.get("summary_chars") or 0)
    if method == "B3":
        return base + sum(len(row.get("text") or "") for row in result.get("retrieval", {}).get("chunks", []))
    return base


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=OLD20)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--summary-workers", type=int, default=1)
    parser.add_argument("--manifest", type=Path, default=ROOT / "config" / "dqa30_frozen_graphs.json")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa30_frozen_old20_baselines9b")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    frozen = json.loads(args.manifest.read_text(encoding="utf-8"))
    graph_records = {row["novel"]: row for row in frozen["records"]}
    q0 = csv_index(ROOT / "outputs" / "four_datasets" / "dqa_local_c16_consensus20" / "per_question.csv", ("novel", "qid"))
    graph_methods = csv_index(ROOT / "outputs" / "four_datasets" / "dqa_local_c24_pure9_consensus20" / "per_question.csv", ("novel", "qid"))
    cases = merged_cases(args.novels)
    client = NativeOllamaNoThinkClient(args.model, num_ctx=args.num_ctx)
    total = sum(len(cases[novel]["questions"]) for novel in args.novels)
    completed = 0
    started = time.time()
    run_manifest = {
        "version": VERSION,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": args.model,
        "thinking": "disabled",
        "num_ctx": args.num_ctx,
        "methods": ["Q0", "B1", "B2", "B3"],
        "frozen_graph_manifest": str(args.manifest.resolve()),
        "novels": args.novels,
        "ordinary_rag_guard": "B3 uses original chunks only; graph links are disabled.",
    }
    (args.out / "manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    for novel in args.novels:
        record = graph_records[novel]
        if record["cohort"] != "old20":
            raise RuntimeError(f"not an old20 frozen graph: {novel}")
        graph_path = Path(record["graph_path"])
        if sha256(graph_path) != record["sha256"]:
            raise RuntimeError(f"frozen graph drift: {novel}")
        case = cases[novel]
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        queries = [
            f"Question: {question['question']}\nCandidate answer: {choice}"
            for question in case["questions"] for choice in question["choices"][:4]
        ]
        vectors = embed(queries)
        compression_dir = args.out / "compression" / novel
        compression_dir.mkdir(parents=True, exist_ok=True)
        compressed = compress_novel_parallel(client, case["text"], compression_dir, args.summary_workers)
        if not compressed.strip():
            raise RuntimeError(f"empty 9B compression: {novel}")
        for qi, question in enumerate(case["questions"]):
            answer_path = args.out / "answers" / novel / f"q{qi:02d}.json"
            if valid(answer_path, record["sha256"]):
                completed += 1
                continue
            key = (novel, question["qid"])
            if key not in q0 or key not in graph_methods:
                raise RuntimeError(f"missing frozen Q0/tail row: {key}")
            task_started = time.time()
            answers = {
                "Q0": {"selected_letter": q0[key]["closed35"], "source": "dqa_local_c16_consensus20/per_question.csv"},
                "B1": {"selected_letter": graph_methods[key]["tail"], "source": "dqa_local_c24_pure9_consensus20/per_question.csv", "tail_chars": 50_000},
            }
            answers["B2"] = answer_compressed(client, question, compressed)
            answers["B3"] = answer_evidence(
                client,
                question,
                ctx,
                matrix,
                vectors[qi * 4: qi * 4 + 4],
                include_graph=False,
            )
            for method, result in answers.items():
                chars = estimated_chars(question, result, method)
                result["input_characters"] = chars
                result["estimated_input_tokens"] = round(chars / 4)
                result["token_accounting"] = "character-based estimate; exact Ollama counts unavailable for reused legacy predictions"
            invalid = [method for method, result in answers.items()
                       if not (result.get("selected_letter") and result["selected_letter"] in LETTERS)]
            if invalid:
                raise RuntimeError(f"invalid selections {novel}/q{qi}: {invalid}")
            gold = LETTERS[question["gold_index"]]
            row = {
                "version": VERSION,
                "model": args.model,
                "thinking": "disabled",
                "graph_sha256": record["sha256"],
                "novel": novel,
                "qi": qi,
                "qid": question["qid"],
                "question": question["question"],
                "choices": question["choices"],
                "gold_letter": gold,
                "answers": answers,
                "correct": {method: result["selected_letter"] == gold for method, result in answers.items()},
                "elapsed_seconds_new_calls": time.time() - task_started,
            }
            answer_path.parent.mkdir(parents=True, exist_ok=True)
            answer_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
            completed += 1
            elapsed = max(time.time() - started, 0.01)
            progress = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "completed_questions": completed,
                "total_questions": total,
                "current": f"{novel}/q{qi}",
                "per_hour": completed / elapsed * 3600,
                "eta_minutes": (total - completed) / max(completed / elapsed, 1e-9) / 60,
            }
            (args.out / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[{completed}/{total}] {novel}/q{qi} B2={answers['B2']['selected_letter']} B3={answers['B3']['selected_letter']}", flush=True)
    run_manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    run_manifest["questions"] = total
    (args.out / "manifest.json").write_text(json.dumps(run_manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
