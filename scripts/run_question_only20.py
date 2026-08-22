"""Question-and-options-only Qwen baseline for the combined 20 DetectiveQA novels."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from build_c_next10_graphs import merged_cases
from c_option_methods import LETTERS, normalize_letter, question_type
from eval_four_datasets import OllamaClient
from novel_kg_studio.cache import load_json, save_json

NOVELS = ["26", "27", "28", "30", "31", "33", "40", "53", "56", "79", "15", "16", "25", "29", "81", "82", "83", "84", "87", "90"]
SYSTEM = (
    "You answer a multiple-choice question using only the question and answer options shown. "
    "No novel passage, knowledge graph, retrieval result, metadata, or gold evidence is available. "
    "Do not claim to have seen omitted context. Select exactly one most likely option and return strict JSON only."
)
SCHEMA = '{"selected_letter":"A|B|C|D","reason":"brief reason based only on question and option wording"}'


def prompt(q: dict[str, Any]) -> str:
    options = "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(q["choices"][:4]))
    return f"Question:\n{q['question']}\n\nOptions:\n{options}\n\nReturn {SCHEMA}"


def valid(path: Path) -> bool:
    try:
        return normalize_letter((load_json(path) or {}).get("selected_letter")) is not None
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_question_only20")
    parser.add_argument("--model", default="qwen2.5:7b-32k")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    cases = merged_cases(NOVELS)
    jobs: list[tuple[str, int, dict[str, Any], Path]] = []
    cached_rows: list[dict[str, Any]] = []
    for novel in NOVELS:
        for qi, q in enumerate(cases[novel]["questions"]):
            path = args.out_root / "answers" / novel / f"q{qi:02d}.json"
            if valid(path):
                cached_rows.append(load_json(path))
            else:
                jobs.append((novel, qi, q, path))
    total = sum(len(cases[n]["questions"]) for n in NOVELS)
    started = time.time()

    def write_progress(completed: int, current: str) -> None:
        save_json(
            args.out_root / "progress.json",
            {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "completed": completed,
                "total": total,
                "remaining": total - completed,
                "current": current,
                "elapsed_minutes": round((time.time() - started) / 60, 2),
            },
        )

    write_progress(len(cached_rows), "starting")

    def one(job: tuple[str, int, dict[str, Any], Path]) -> dict[str, Any]:
        novel, qi, q, path = job
        client = OllamaClient(args.model, max_tokens=350, num_ctx=4096)
        raw = client.complete_json(SYSTEM, prompt(q), max_tokens=350)
        letter = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else None)
        result = {
            "novel": novel,
            "qi": qi,
            "qid": q["qid"],
            "question": q["question"],
            "choices": q["choices"],
            "gold_index": q.get("gold_index"),
            "gold_letter": LETTERS[q["gold_index"]],
            "question_type": question_type(q["question"]),
            "selected_letter": letter,
            "selected_text": q["choices"][LETTERS.index(letter)] if letter in LETTERS else "",
            "correct": letter == LETTERS[q["gold_index"]],
            "reason": str(raw.get("reason", "")) if isinstance(raw, dict) else "",
            "raw": raw,
            "model": args.model,
            "input_policy": "question_and_options_only",
            "prompt_hash": hashlib.sha256((SYSTEM + SCHEMA).encode("utf-8")).hexdigest()[:16],
        }
        save_json(path, result)
        return result

    completed = len(cached_rows)
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(jobs) or 1))) as pool:
        futures = [pool.submit(one, job) for job in jobs]
        for future in as_completed(futures):
            row = future.result()
            completed += 1
            current = f"novel {row['novel']} / q{row['qi']} / {row['selected_letter']}"
            write_progress(completed, current)
            print(f"[{completed}/{total}] {current}", flush=True)
    rows = []
    for novel in NOVELS:
        for qi, _ in enumerate(cases[novel]["questions"]):
            rows.append(load_json(args.out_root / "answers" / novel / f"q{qi:02d}.json"))
    summary = {
        "completed": len(rows),
        "total": total,
        "parsed": sum(normalize_letter(r.get("selected_letter")) is not None for r in rows),
        "correct": sum(bool(r.get("correct")) for r in rows),
        "accuracy": sum(bool(r.get("correct")) for r in rows) / total,
        "model": args.model,
        "input_policy": "question_and_options_only",
        "prompt_hash": rows[0]["prompt_hash"],
    }
    save_json(args.out_root / "summary.json", summary)
    write_progress(total, "complete")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
