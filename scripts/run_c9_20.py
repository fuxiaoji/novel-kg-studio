"""Run matched 50k-tail and equal-budget C9 hybrid on the trusted novels."""

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
from c8_graph_passage import LETTERS, normalize_letter  # noqa: E402
from c9_hybrid import VERSION, run_c9  # noqa: E402
from eval_four_datasets import OllamaClient  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, graph_path  # noqa: E402

METHODS = ("tail50", "hybrid")


def source_hash() -> str:
    return hashlib.sha256((ROOT / "scripts" / "c9_hybrid.py").read_bytes()).hexdigest()[:16]


def answer_path(root: Path, method: str, novel: str, qi: int) -> Path:
    return root / method / novel / f"q{qi:02d}.json"


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
        self.started = time.time()
        self.lock = threading.Lock()

    def record(self, current: str) -> None:
        with self.lock:
            self.done += 1
            elapsed = max(time.time() - self.started, 0.01)
            payload = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"), "completed": self.done, "total": self.total,
                "current": current, "per_hour": self.done / elapsed * 3600,
                "eta_minutes": (self.total - self.done) / max(self.done / elapsed, 1e-9) / 60,
            }
            (self.root / "progress.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=list(METHODS))
    parser.add_argument("--model", default="qwen2.5:7b-32k")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c9_20")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    progress = Progress(args.out, sum(len(cases[n]["questions"]) for n in args.novels) * len(args.methods))
    client = OllamaClient(args.model, max_tokens=600, num_ctx=32768)
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "version": VERSION, "source_hash": source_hash(), "model": args.model, "methods": args.methods, "novels": args.novels, "budget": "34 source chunks for both methods"}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for method in args.methods:
        for novel in args.novels:
            case = cases[novel]
            graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
            jobs = []
            for qi, q in enumerate(case["questions"]):
                path = answer_path(args.out, method, novel, qi)
                if valid(path):
                    progress.record(f"cached {method}/{novel}/q{qi}")
                else:
                    jobs.append((qi, q, path))

            def one(job: tuple[int, dict[str, Any], Path]) -> dict[str, Any]:
                qi, q, path = job
                row = run_c9(method, client, q, graph, case["text"])
                row.update({"novel": novel, "batch": "first10" if novel in FIRST10 else "second10", "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": LETTERS[q["gold_index"]], "correct": row["selected_letter"] == LETTERS[q["gold_index"]], "answer_model": args.model, "source_hash": source_hash()})
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
                return row

            with ThreadPoolExecutor(max_workers=min(args.workers, len(jobs) or 1)) as pool:
                futures = [pool.submit(one, job) for job in jobs]
                for future in as_completed(futures):
                    row = future.result()
                    progress.record(f"{method}/{novel}/q{row['qi']} -> {row['selected_letter']}")
                    print(f"[{progress.done}/{progress.total}] {method}/{novel}/q{row['qi']} -> {row['selected_letter']}", flush=True)
    manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
