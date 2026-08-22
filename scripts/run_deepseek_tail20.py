"""Unmasked 50k-character tail baseline with DeepSeek V4 Flash, thinking disabled."""

from __future__ import annotations

import argparse
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
from run_c13c_deepseek import DeepSeekNoThinkingClient  # noqa: E402
from run_c8_20 import NOVELS  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
VERSION = "deepseek-v4flash-tail50k-thinking-disabled-v1"
TAIL_CHARS = 50_000


def answer_one(client: DeepSeekNoThinkingClient, q: dict[str, Any], text: str) -> dict[str, Any]:
    options = "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(q["choices"][:4]))
    raw = client.complete_json(
        "Answer only from the supplied tail of the detective novel. Output JSON only.",
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options}\n\nNOVEL TAIL\n{text[-TAIL_CHARS:]}\n\n"
        'Return only {"selected_letter":"A|B|C|D"}. Obey NOT/EXCEPT/incorrect wording.',
        max_tokens=80,
    )
    return {
        "method": "tail_window_50000_chars",
        "selected_letter": normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw),
        "raw": raw,
        "prompt_version": VERSION,
        "mask": "unmasked",
        "thinking": "disabled",
        "tail_chars": TAIL_CHARS,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--out", type=Path, default=BASE / "dqa_deepseek_tail50k_20")
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    client = DeepSeekNoThinkingClient(args.model)
    jobs = []
    for novel in args.novels:
        for qi, q in enumerate(cases[novel]["questions"]):
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            if path.exists():
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                    if row.get("prompt_version") == VERSION and normalize_letter(row.get("selected_letter")) in LETTERS:
                        continue
                except Exception:
                    pass
            jobs.append((novel, qi, q, cases[novel]["text"], path))
    total = sum(len(cases[n]["questions"]) for n in args.novels)
    completed = total - len(jobs)
    lock = threading.Lock()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(json.dumps({"version": VERSION, "model": args.model, "thinking": "disabled", "mask": "unmasked", "tail_chars": TAIL_CHARS, "novels": args.novels}, indent=1), encoding="utf-8")

    def one(job: tuple[str, int, dict[str, Any], str, Path]) -> dict[str, Any]:
        novel, qi, q, text, path = job
        row = answer_one(client, q, text)
        gold = LETTERS[q["gold_index"]]
        row.update({"novel": novel, "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": gold, "correct": row["selected_letter"] == gold, "answer_model": args.model})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
        return row

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(one, job) for job in jobs]
        for future in as_completed(futures):
            row = future.result()
            with lock:
                completed += 1
                print(f"[{completed}/{total}] tail/{row['novel']}/q{row['qi']} -> {row['selected_letter']}", flush=True)
    selected = set(args.novels)
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in (args.out / "answers").glob("*/*.json") if p.parent.name in selected]
    correct = sum(bool(row["correct"]) for row in rows)
    print(json.dumps({"correct": correct, "total": len(rows), "accuracy": correct / len(rows)}, indent=1))


if __name__ == "__main__":
    main()
