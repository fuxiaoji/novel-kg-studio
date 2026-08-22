"""Auditable metrics for C15/C16 local-small-model experiments."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
OUT = BASE / "dqa_local_c16_consensus20"


def load(root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in root.glob("*/*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        result[row["qid"]] = row
    return result


def score(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    correct = sum(row[method] == row["gold"] for row in rows)
    return {"correct": correct, "total": len(rows), "accuracy": correct / len(rows)}


def paired(rows: list[dict[str, Any]], new: str, base: str) -> dict[str, Any]:
    wins = sum(row[new] == row["gold"] and row[base] != row["gold"] for row in rows)
    losses = sum(row[base] == row["gold"] and row[new] != row["gold"] for row in rows)
    n = wins + losses
    p = min(1.0, 2 * sum(math.comb(n, k) for k in range(min(wins, losses) + 1)) / 2**n) if n else 1.0
    return {"wins": wins, "losses": losses, "exact_mcnemar_p": p}


def cluster_ci(rows: list[dict[str, Any]], new: str, base: str, iterations: int = 20_000) -> list[float]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by[row["novel"]].append(row)
    novels = sorted(by, key=int)
    rng = random.Random(20260811)
    values = []
    for _ in range(iterations):
        sample = [row for novel in [rng.choice(novels) for _ in novels] for row in by[novel]]
        values.append(100 * sum((row[new] == row["gold"]) - (row[base] == row["gold"]) for row in sample) / len(sample))
    values.sort()
    return [values[int(0.025 * iterations)], values[int(0.975 * iterations)]]


def main() -> None:
    c16 = load(OUT / "answers")
    tail = load(BASE / "dqa_qwen35_c15_20" / "answers" / "tail")
    graph = load(BASE / "dqa_qwen35_c15_20" / "answers" / "graph")
    closed35 = load(BASE / "dqa_qwen35_c15_20" / "answers" / "question_only")
    c12 = load(BASE / "dqa_qwen_c12_consensus20" / "answers")
    closed25 = load(BASE / "dqa_qwen_question_only20" / "answers")
    qtypes = {row["qid"]: row["question_type"] for row in csv.DictReader((BASE / "dqa_qwen_c_combined20" / "per_question_matrix.csv").open(encoding="utf-8-sig"))}
    assert all(len(block) == 164 for block in (c16, tail, graph, closed35, c12, closed25))
    rows = []
    for qid, result in c16.items():
        rows.append({
            "qid": qid, "novel": result["novel"], "batch": result["batch"], "question_type": qtypes[qid], "gold": result["gold_letter"],
            "closed35": closed35[qid]["selected_letter"], "closed25": closed25[qid]["selected_letter"], "tail35": tail[qid]["selected_letter"],
            "graph35": graph[qid]["selected_letter"], "c12": c12[qid]["selected_letter"], "c16": result["selected_letter"], "route": result["route"],
        })
    first = [row for row in rows if row["batch"] == "first10"]
    second = [row for row in rows if row["batch"] == "second10"]
    hard35 = [row for row in rows if row["closed35"] != row["gold"]]
    hard_both = [row for row in hard35 if row["closed25"] != row["gold"]]
    easy35 = [row for row in rows if row["closed35"] == row["gold"]]
    methods = ("closed35", "tail35", "graph35", "c12", "c16")
    summary = {
        "metadata": {"novels": 20, "questions": 164, "external_api": False, "mask": "unmasked", "models": ["qwen3.5:9b Q4_K_M", "qwen2.5:7b-32k Q3_K_M"]},
        "all": {method: score(rows, method) for method in methods},
        "first10_development": {method: score(first, method) for method in methods},
        "second10_frozen_validation": {method: score(second, method) for method in methods},
        "qwen35_question_only_wrong": {method: score(hard35, method) for method in ("tail35", "graph35", "c12", "c16")},
        "both_closed_models_wrong": {method: score(hard_both, method) for method in ("tail35", "graph35", "c12", "c16")},
        "qwen35_question_only_correct_preservation": {method: score(easy35, method) for method in ("tail35", "graph35", "c12", "c16")},
        "paired": {"c16_vs_tail35": paired(rows, "c16", "tail35"), "c16_vs_c12": paired(rows, "c16", "c12"), "graph35_vs_tail35": paired(rows, "graph35", "tail35")},
        "cluster_bootstrap_95_points": {"c16_minus_tail35": cluster_ci(rows, "c16", "tail35"), "c16_minus_c12": cluster_ci(rows, "c16", "c12")},
        "routes": {}, "by_question_type": {}, "by_novel": {},
    }
    for route in sorted({row["route"] for row in rows}):
        subset = [row for row in rows if row["route"] == route]
        summary["routes"][route] = {method: score(subset, method) for method in ("tail35", "graph35", "c12", "c16")}
    for key in sorted({row["question_type"] for row in rows}):
        subset = [row for row in rows if row["question_type"] == key]
        summary["by_question_type"][key] = {method: score(subset, method) for method in ("tail35", "graph35", "c12", "c16")}
    for novel in sorted({row["novel"] for row in rows}, key=int):
        subset = [row for row in rows if row["novel"] == novel]
        summary["by_novel"][novel] = {method: score(subset, method) for method in ("tail35", "graph35", "c12", "c16")}
    (OUT / "analysis.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT / "per_question.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(sorted(rows, key=lambda row: (int(row["novel"]), row["qid"])))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
