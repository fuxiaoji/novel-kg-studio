"""Run resumable unmasked C13 option-conditioned graph rebuttal experiments."""

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

from analyze_dense_retrieval import chunk_embeddings  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c13_option_rebuttal import VERSION, run_c13  # noqa: E402
from c8_graph_passage import C8Context, LETTERS, normalize_letter  # noqa: E402
from eval_four_datasets import OllamaClient  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, graph_path  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"


def source_hash() -> str:
    return hashlib.sha256((ROOT / "scripts" / "c13_option_rebuttal.py").read_bytes()).hexdigest()[:16]


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
            data = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "completed": self.done,
                "total": self.total,
                "current": current,
                "per_hour": self.done / elapsed * 3600,
                "eta_minutes": (self.total - self.done) / max(self.done / elapsed, 1e-9) / 60,
            }
            (self.root / "progress.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen2.5:7b-32k")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--option-workers", type=int, default=2)
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--out", type=Path, default=BASE / "dqa_qwen_c13_20")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    total = sum(min(len(cases[n]["questions"]), args.max_questions or 10**9) for n in args.novels)
    progress = Progress(args.out, total)
    client = OllamaClient(args.model, max_tokens=600, num_ctx=32768)
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "source_hash": source_hash(),
        "model": args.model,
        "novels": args.novels,
        "mask": "unmasked",
        "definition": "option-conditioned BGE-M3 + BM25 + graph-edge RRF; independent support/rebuttal quote extraction; conservative quote arbitration",
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        jobs = []
        for qi, q in enumerate(case["questions"][: args.max_questions or None]):
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            if path.exists():
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                    if row.get("prompt_version") == VERSION and row.get("source_hash") == source_hash() and normalize_letter(row.get("selected_letter")) in LETTERS:
                        progress.record(f"cached {novel}/q{qi}")
                        continue
                except Exception:
                    pass
            jobs.append((qi, q, path))

        def one(job: tuple[int, dict[str, Any], Path]) -> dict[str, Any]:
            qi, q, path = job
            row = run_c13(client, q, graph, case["text"], matrix, args.option_workers)
            selected = normalize_letter(row.get("selected_letter"))
            row.update(
                {
                    "novel": novel,
                    "batch": "first10" if novel in FIRST10 else "second10",
                    "qi": qi,
                    "qid": q["qid"],
                    "question": q["question"],
                    "choices": q["choices"],
                    "gold_letter": LETTERS[q["gold_index"]],
                    "selected_letter": selected,
                    "correct": selected == LETTERS[q["gold_index"]],
                    "mask": "unmasked",
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
                progress.record(f"c13/{novel}/q{row['qi']} -> {row['selected_letter']}")
                print(f"[{progress.done}/{progress.total}] c13/{novel}/q{row['qi']} -> {row['selected_letter']}", flush=True)

    rows = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((args.out / "answers").glob("*/*.json"))]
    selected_novels = set(args.novels)
    rows = [row for row in rows if row["novel"] in selected_novels]
    summary = {
        "correct": sum(bool(row["correct"]) for row in rows),
        "total": len(rows),
        "accuracy": sum(bool(row["correct"]) for row in rows) / len(rows) if rows else 0.0,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
