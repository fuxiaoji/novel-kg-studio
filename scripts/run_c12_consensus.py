"""C12: deterministic unmasked multi-view graph consensus over trusted 20 novels."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_c_combined20 import load_rows  # noqa: E402
from c_option_methods import LETTERS, normalize_letter  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
VERSION = "c12-unmasked-tail-c4-c6-c8-consensus-v1"


def load_c8_graph() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    root = BASE / "dqa_qwen_c8_20" / "graph" / "unmasked"
    for path in root.glob("*/*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        result[row["qid"]] = row
    return result


def consensus(votes: dict[str, str | None]) -> tuple[str | None, dict[str, int]]:
    clean = {name: normalize_letter(value) for name, value in votes.items()}
    counts = Counter(value for value in clean.values() if value and value in LETTERS)
    if not counts:
        return None, {}
    best_count = max(counts.values())
    winners = sorted(letter for letter, count in counts.items() if count == best_count)
    # C4 is the most stable single graph method across the two batches and is
    # used only for deterministic tie-breaking; no confidence or gold labels.
    c4 = clean.get("c4")
    selected = c4 if c4 in winners else winners[0]
    return selected, dict(sorted(counts.items()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=BASE / "dqa_qwen_c12_consensus20")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    c8 = load_c8_graph()
    rows = load_rows()
    outputs = []
    for old in rows:
        qid = old["qid"]
        votes = {
            "tail": old["tail_unmasked"],
            "c4": old["c4_unmasked"],
            "c6": old["c6_unmasked"],
            "c8_graph": c8[qid].get("selected_letter"),
        }
        selected, counts = consensus(votes)
        row = {
            "version": VERSION,
            "novel": old["novel"],
            "batch": old["batch"],
            "qi": int(old["qi"]),
            "qid": qid,
            "question": old["question"],
            "gold_letter": old["gold_letter"],
            "selected_letter": selected,
            "correct": selected == old["gold_letter"],
            "votes": votes,
            "vote_counts": counts,
            "tie_breaker": "c4",
            "mask": "unmasked",
        }
        path = args.out / "answers" / old["novel"] / f"q{int(old['qi']):02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
        outputs.append(row)

    def summary(subset: list[dict[str, Any]]) -> dict[str, Any]:
        correct = sum(bool(row["correct"]) for row in subset)
        return {"correct": correct, "total": len(subset), "accuracy": correct / len(subset) if subset else 0.0}

    result = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "definition": "unmasked majority vote of tail, C4 grounded graph, C6 graph evidence arbiter, and C8 verified graph overlay; C4 breaks ties",
        "all": summary(outputs),
        "first10": summary([row for row in outputs if row["batch"] == "first10"]),
        "second10": summary([row for row in outputs if row["batch"] == "second10"]),
    }
    (args.out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
