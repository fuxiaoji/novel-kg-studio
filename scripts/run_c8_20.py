"""Run C8 graph-guided passage retrieval and a matched BM25 baseline on 20 novels."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from build_c_next10_graphs import merged_cases  # noqa: E402
from c8_graph_passage import LETTERS, VERSION, normalize_letter, run_c8  # noqa: E402
from eval_four_datasets import OllamaClient  # noqa: E402

FIRST10 = ["26", "27", "28", "30", "31", "33", "40", "53", "56", "79"]
SECOND10 = ["15", "16", "25", "29", "81", "82", "83", "84", "87", "90"]
NOVELS = FIRST10 + SECOND10
METHODS = ("bm25", "graph")


def graph_path(novel: str) -> Path:
    root = ROOT / "outputs" / "four_datasets" / ("dqa_qwen_c" if novel in FIRST10 else "dqa_qwen_c_next10")
    return root / "novels" / novel / "graph.json"


def source_hash() -> str:
    return hashlib.sha256((ROOT / "scripts" / "c8_graph_passage.py").read_bytes()).hexdigest()[:16]


def answer_path(root: Path, method: str, novel: str, qi: int) -> Path:
    return root / method / "unmasked" / novel / f"q{qi:02d}.json"


def valid(path: Path) -> bool:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return row.get("prompt_version") == VERSION and row.get("source_hash") == source_hash() and normalize_letter(row.get("selected_letter")) in LETTERS
    except Exception:
        return False


class Progress:
    def __init__(self, root: Path, total: int) -> None:
        self.root = root
        self.total = total
        self.done = 0
        self.lock = threading.Lock()
        self.started = time.time()

    def record(self, current: str) -> None:
        with self.lock:
            self.done += 1
            elapsed = max(time.time() - self.started, 0.01)
            payload = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "completed": self.done,
                "total": self.total,
                "current": current,
                "per_hour": self.done / elapsed * 3600,
                "eta_minutes": (self.total - self.done) / max(self.done / elapsed, 1e-9) / 60,
            }
            (self.root / "progress.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def run_method(args: argparse.Namespace, method: str, cases: dict[str, Any], progress: Progress) -> None:
    client = OllamaClient(args.model, max_tokens=600, num_ctx=32768)
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        jobs = []
        for qi, q in enumerate(case["questions"][: args.max_questions or None]):
            path = answer_path(args.out, method, novel, qi)
            if valid(path):
                progress.record(f"cached {method}/{novel}/q{qi}")
            else:
                jobs.append((qi, q, path))

        def one(job: tuple[int, dict[str, Any], Path]) -> dict[str, Any]:
            qi, q, path = job
            row = run_c8(method, client, q, graph, case["text"], None)
            row.update(
                {
                    "novel": novel,
                    "batch": "first10" if novel in FIRST10 else "second10",
                    "qi": qi,
                    "qid": q["qid"],
                    "question": q["question"],
                    "choices": q["choices"],
                    "gold_index": q["gold_index"],
                    "gold_letter": LETTERS[q["gold_index"]],
                    "correct": row["selected_letter"] == LETTERS[q["gold_index"]],
                    "answer_model": args.model,
                    "source_hash": source_hash(),
                }
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
            return row

        with ThreadPoolExecutor(max_workers=min(args.workers, len(jobs) or 1)) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for future in as_completed(futures):
                row = future.result()
                progress.record(f"{method}/{novel}/q{row['qi']} -> {row['selected_letter']}")
                print(f"[{progress.done}/{progress.total}] {method}/{novel}/q{row['qi']} -> {row['selected_letter']}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c8_20")
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen2.5:7b-32k")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-questions", type=int, default=0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    total_questions = sum(min(len(cases[n]["questions"]), args.max_questions or 10**9) for n in args.novels)
    progress = Progress(args.out, total_questions * len(args.methods))
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "source_hash": source_hash(),
        "model": args.model,
        "methods": args.methods,
        "novels": args.novels,
        "masks": ["unmasked"],
        "graph_policy": "read-only existing graphs",
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for method in args.methods:
        run_method(args, method, cases, progress)
    manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
