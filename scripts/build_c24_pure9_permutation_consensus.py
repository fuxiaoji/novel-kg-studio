"""C24: pure-Qwen3.5-9B three-permutation graph consensus with 9B tail fallback."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
OUT = BASE / "dqa_local_c24_pure9_consensus20"
FIRST10 = {"26", "27", "28", "30", "31", "33", "40", "53", "56", "79"}
VERSION = "c24-pure-qwen35-9b-three-permutation-majority-tail-fallback-v1"


def load(root: Path) -> dict[str, dict]:
    rows = {}
    for path in root.rglob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("qid"):
                rows[row["qid"]] = row
        except Exception:
            continue
    return rows


def score(rows: list[dict], key: str) -> dict:
    correct = sum(row[key] == row["gold"] for row in rows)
    return {"correct": correct, "total": len(rows), "accuracy": correct / len(rows)}


def paired(rows: list[dict], key: str, baseline: str) -> dict:
    wins = sum(row[key] == row["gold"] and row[baseline] != row["gold"] for row in rows)
    losses = sum(row[key] != row["gold"] and row[baseline] == row["gold"] for row in rows)
    n = wins + losses
    tail = sum(math.comb(n, k) for k in range(min(wins, losses) + 1)) / 2**n if n else 0.5
    return {"wins": wins, "losses": losses, "exact_mcnemar_p": min(1.0, 2 * tail)}


def main() -> None:
    tail = load(BASE / "dqa_qwen35_c15_20" / "answers" / "tail")
    original = load(BASE / "dqa_qwen35_c15_20" / "answers" / "graph")
    reversed_order = load(BASE / "dqa_local_c21_20" / "answers")
    cyclic = load(BASE / "dqa_local_c23_cyclic20" / "answers")
    qids = sorted(set(tail) & set(original) & set(reversed_order) & set(cyclic))
    if len(qids) != 164:
        raise RuntimeError(f"expected 164 questions, got {len(qids)}")
    rows = []
    for qid in qids:
        graph_votes = [original[qid].get("selected_letter"), reversed_order[qid].get("selected_letter"), cyclic[qid].get("selected_letter")]
        counts = Counter(letter for letter in graph_votes if isinstance(letter, str) and letter in "ABCD")
        majority_letter, majority_count = counts.most_common(1)[0]
        selected = majority_letter if majority_count >= 2 else tail[qid]["selected_letter"]
        route = "three_permutation_graph_majority" if majority_count >= 2 else "qwen35_tail_fallback"
        gold = original[qid]["gold_letter"]
        output = {
            "version": VERSION, "model": "qwen3.5:9b", "thinking": "disabled", "external_api": False, "mask": "unmasked",
            "novel": original[qid]["novel"], "batch": "first10" if original[qid]["novel"] in FIRST10 else "second10", "qi": original[qid]["qi"], "qid": qid,
            "question": original[qid]["question"], "choices": original[qid]["choices"], "gold_letter": gold, "selected_letter": selected, "correct": selected == gold,
            "route": route, "votes": {"graph_original": graph_votes[0], "graph_reversed_mapped": graph_votes[1], "graph_cyclic_mapped": graph_votes[2], "tail": tail[qid]["selected_letter"]},
        }
        path = OUT / "answers" / output["novel"] / f"q{output['qi']:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append({"novel": output["novel"], "batch": output["batch"], "qi": output["qi"], "qid": qid, "gold": gold, "tail": tail[qid]["selected_letter"], "original": graph_votes[0], "reversed": graph_votes[1], "cyclic": graph_votes[2], "c24": selected, "route": route})
    first = [row for row in rows if row["batch"] == "first10"]
    second = [row for row in rows if row["batch"] == "second10"]
    keys = ["tail", "original", "reversed", "cyclic", "c24"]
    report = {
        "metadata": {"version": VERSION, "single_parameter_model": True, "answer_models": ["qwen3.5:9b"], "questions": 164, "novels": 20, "external_api": False, "mask": "unmasked"},
        "all": {key: score(rows, key) for key in keys},
        "first10_development": {key: score(first, key) for key in keys},
        "second10_frozen_validation": {key: score(second, key) for key in keys},
        "paired": {baseline: paired(rows, "c24", baseline) for baseline in ("tail", "original")},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / "per_question.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
