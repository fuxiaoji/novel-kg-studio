"""Thirty-novel audit and graph-only confidence routing for frozen G7."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
G7 = BASE / "dqa30_attention" / "g7_pure_graph_tight" / "answers"
OLD = BASE / "dqa_local_c24_pure9_consensus20" / "per_question.csv"
NEW = BASE / "dqa30_attention" / "batch03_eval" / "answers"
OUT = ROOT / "reports" / "DQA30_G7_GRAPH_ONLY_ANALYSIS_20260824.json"


def majority(row):
    votes = [row["P1"], row["P2"], row["P3"]]
    counts = Counter(votes)
    best = max(counts.values())
    tied = sorted(letter for letter, count in counts.items() if count == best)
    return row["P1"] if row["P1"] in tied else tied[0]


def load_rows():
    old = {}
    with OLD.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            old[row["qid"]] = row
    rows = []
    for path in sorted(G7.rglob("q*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        qid = item["qid"]
        if qid in old:
            source = old[qid]
            p1, p2, p3, tail = source["original"], source["reversed"], source["cyclic"], source["tail"]
            cohort = "old20"
        else:
            source = json.loads((NEW / str(item["novel"]) / path.name).read_text(encoding="utf-8"))
            p1 = source["answers"]["G1"]["selected_letter"]
            p2 = source["answers"]["G2"]["selected_letter"]
            p3 = source["answers"]["G3"]["selected_letter"]
            tail = source["answers"]["B1"]["selected_letter"]
            cohort = "new10"
        diag = item["retrieval"]["diagnostics"]
        rows.append({
            "novel": str(item["novel"]), "qid": qid, "cohort": cohort,
            "gold": item["gold_letter"], "G7": item["selected_letter"], "TAIL": tail,
            "P1": p1, "P2": p2, "P3": p3,
            "confidence": str(item.get("confidence") or "low").lower(),
            "links": int(diag["valid_relation_count"]),
            "removed": int(diag["removed_relation_count"]),
            "margin": float(diag["graph_margin"]),
            "baseline_access": bool(item.get("baseline_access")),
        })
    return rows


def rules():
    return {
        "G7": lambda r: r["G7"],
        "PERM": majority,
        "G7_high_else_PERM": lambda r: r["G7"] if r["confidence"] == "high" else majority(r),
        "G7_if_perm_unstable_else_PERM": lambda r: r["G7"] if len({r["P1"], r["P2"], r["P3"]}) > 1 else majority(r),
        "PERM_if_unanimous_else_G7": lambda r: majority(r) if len({r["P1"], r["P2"], r["P3"]}) == 1 else r["G7"],
        "G7_agrees_any_else_PERM": lambda r: r["G7"] if r["G7"] in {r["P1"], r["P2"], r["P3"]} else majority(r),
        "G7_agrees_majority_else_PERM": lambda r: r["G7"] if r["G7"] == majority(r) else majority(r),
        "G7_links6_else_PERM": lambda r: r["G7"] if r["links"] >= 6 else majority(r),
        "G7_low_removed_else_PERM": lambda r: r["G7"] if r["removed"] <= 2 else majority(r),
        "G7_margin_positive_else_PERM": lambda r: r["G7"] if r["margin"] > 0 else majority(r),
    }


def score(rows, key):
    correct = sum(r[key] == r["gold"] for r in rows)
    return {"correct": correct, "total": len(rows), "accuracy": correct / len(rows)}


def mcnemar(rows, key, baseline):
    wins = sum(r[key] == r["gold"] and r[baseline] != r["gold"] for r in rows)
    losses = sum(r[key] != r["gold"] and r[baseline] == r["gold"] for r in rows)
    n = wins + losses
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(min(wins, losses) + 1)) / 2**n) if n else 1.0
    return {"wins": wins, "losses": losses, "exact_p": p}


def main():
    rows = load_rows()
    candidates = rules()
    fixed = {}
    for name, rule in candidates.items():
        evaluated = [{**r, name: rule(r)} for r in rows]
        fixed[name] = {**score(evaluated, name), "vs_tail": mcnemar(evaluated, name, "TAIL")}
    lono = []
    selected = Counter()
    priority = list(candidates)
    for novel in sorted({r["novel"] for r in rows}, key=int):
        train = [r for r in rows if r["novel"] != novel]
        test = [r for r in rows if r["novel"] == novel]
        best = min(candidates, key=lambda n: (-sum(candidates[n](r) == r["gold"] for r in train), priority.index(n)))
        selected[best] += 1
        lono.extend({**r, "LONO": candidates[best](r), "selected_rule": best} for r in test)
    report = {
        "metadata": {"model": "qwen3.5:9b", "novels": 30, "questions": len(rows), "baseline_access_count": sum(r["baseline_access"] for r in rows)},
        "frozen": {"G7": {**score(rows, "G7"), "vs_tail": mcnemar(rows, "G7", "TAIL")}, "TAIL": score(rows, "TAIL")},
        "cohorts": {cohort: {"G7": score([r for r in rows if r["cohort"] == cohort], "G7"), "TAIL": score([r for r in rows if r["cohort"] == cohort], "TAIL")} for cohort in ("old20", "new10")},
        "fixed_graph_only_posthoc": fixed,
        "nested_graph_only_lono": {**score(lono, "LONO"), "vs_tail": mcnemar(lono, "LONO", "TAIL"), "selected_rules": dict(selected)},
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
