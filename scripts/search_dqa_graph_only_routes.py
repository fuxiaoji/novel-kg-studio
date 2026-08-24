"""Search interpretable graph-only confidence routes on DQA new10.

No tail, compression, RAG, or question-only prediction is available to any
candidate rule. Gold labels are used only in leave-one-novel-out calibration.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets" / "dqa30_attention"
OUT = ROOT / "reports" / "DQA_GRAPH_ONLY_ROUTE_SEARCH_20260824.json"


def load_rows():
    rows = []
    for path in sorted((BASE / "g6_graph_expansion" / "answers").rglob("q*.json")):
        g6 = json.loads(path.read_text(encoding="utf-8"))
        base = json.loads((BASE / "batch03_eval" / "answers" / str(g6["novel"]) / path.name).read_text(encoding="utf-8"))
        answers = base["answers"]
        rows.append({
            "novel": str(g6["novel"]), "qid": g6["qid"], "gold": g6["gold_letter"],
            "G6": g6["selected_letter"], "G1": answers["G1"]["selected_letter"],
            "G2": answers["G2"]["selected_letter"], "G3": answers["G3"]["selected_letter"],
            "G5": answers["G5"]["selected_letter"],
            "unanimous": bool(base["attention_features"]["option_order_unanimous"]),
        })
    return rows


def majority(row, methods, fallback):
    counts = Counter(row[m] for m in methods)
    best_n = max(counts.values())
    tied = sorted(letter for letter, count in counts.items() if count == best_n)
    return row[fallback] if row[fallback] in tied else tied[0]


def rules():
    return {
        "G6": lambda r: r["G6"],
        "G5": lambda r: r["G5"],
        "perm3_majority_G1_tie": lambda r: majority(r, ("G1", "G2", "G3"), "G1"),
        "graph5_majority_G6_tie": lambda r: majority(r, ("G1", "G2", "G3", "G5", "G6"), "G6"),
        "G6_G5_agree_else_G1": lambda r: r["G6"] if r["G6"] == r["G5"] else r["G1"],
        "G6_G5_agree_else_perm3": lambda r: r["G6"] if r["G6"] == r["G5"] else majority(r, ("G1", "G2", "G3"), "G1"),
        "G6_any_agree_else_perm3": lambda r: r["G6"] if r["G6"] in {r["G1"], r["G3"], r["G5"]} else majority(r, ("G1", "G2", "G3"), "G1"),
        "G6_two_agree_else_perm3": lambda r: r["G6"] if sum(r["G6"] == r[m] for m in ("G1", "G3", "G5")) >= 2 else majority(r, ("G1", "G2", "G3"), "G1"),
        "G6_if_perm_unstable_else_G5": lambda r: r["G6"] if not r["unanimous"] else r["G5"],
        "G5_if_perm_unstable_else_G6": lambda r: r["G5"] if not r["unanimous"] else r["G6"],
    }


def accuracy(rows, key):
    return sum(row[key] == row["gold"] for row in rows) / len(rows)


def main():
    rows = load_rows()
    candidates = rules()
    fixed = {}
    for name, rule in candidates.items():
        evaluated = [{**row, name: rule(row)} for row in rows]
        fixed[name] = {"correct": sum(row[name] == row["gold"] for row in evaluated), "total": len(rows), "accuracy": accuracy(evaluated, name)}
    lono = []
    selected = Counter()
    priority = list(candidates)
    for novel in sorted({r["novel"] for r in rows}, key=int):
        train = [r for r in rows if r["novel"] != novel]
        test = [r for r in rows if r["novel"] == novel]
        best = min(candidates, key=lambda n: (-sum(candidates[n](r) == r["gold"] for r in train), priority.index(n)))
        selected[best] += 1
        lono.extend({**row, "prediction": candidates[best](row), "rule": best} for row in test)
    report = {
        "constraint": "graph-only inference; baselines inaccessible to rules",
        "fixed_posthoc": fixed,
        "nested_lono": {"correct": sum(r["prediction"] == r["gold"] for r in lono), "total": len(lono), "accuracy": accuracy(lono, "prediction"), "selected_rules": dict(selected)},
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
