"""Run C10 independent option verification with resumable outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from build_c_next10_graphs import merged_cases  # noqa: E402
from c8_graph_passage import LETTERS, normalize_letter  # noqa: E402
from c10_option_verify import VERSION, run_c10  # noqa: E402
from eval_four_datasets import OllamaClient  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, graph_path  # noqa: E402


def source_hash() -> str:
    return hashlib.sha256((ROOT / "scripts" / "c10_option_verify.py").read_bytes()).hexdigest()[:16]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen2.5:7b-32k")
    parser.add_argument("--option-workers", type=int, default=3)
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c10_20")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    total = sum(min(len(cases[n]["questions"]), args.max_questions or 10**9) for n in args.novels)
    client = OllamaClient(args.model, max_tokens=400, num_ctx=32768)
    started = time.time()
    done = 0
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "version": VERSION, "source_hash": source_hash(), "model": args.model, "novels": args.novels, "option_workers": args.option_workers}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        for qi, q in enumerate(case["questions"][: args.max_questions or None]):
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            valid = False
            if path.exists():
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                    valid = old.get("prompt_version") == VERSION and old.get("source_hash") == source_hash() and normalize_letter(old.get("selected_letter")) in LETTERS
                except Exception:
                    pass
            if not valid:
                row = run_c10(client, q, graph, case["text"], args.option_workers)
                row.update({"novel": novel, "batch": "first10" if novel in FIRST10 else "second10", "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": LETTERS[q["gold_index"]], "correct": row["selected_letter"] == LETTERS[q["gold_index"]], "answer_model": args.model, "source_hash": source_hash()})
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
            else:
                row = old
            done += 1
            elapsed = max(time.time() - started, 0.01)
            progress = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "completed": done, "total": total, "current": f"c10/{novel}/q{qi} -> {row.get('selected_letter')}", "per_hour": done / elapsed * 3600, "eta_minutes": (total - done) / max(done / elapsed, 1e-9) / 60}
            (args.out / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[{done}/{total}] c10/{novel}/q{qi} -> {row.get('selected_letter')}", flush=True)
    manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
