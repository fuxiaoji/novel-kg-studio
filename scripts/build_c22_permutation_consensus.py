"""Build and analyze C22 frozen permutation-consistency consensus."""

from __future__ import annotations

import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from c8_graph_passage import question_type  # noqa: E402
from run_c8_20 import FIRST10  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
OUT = BASE / "dqa_local_c22_permutation_consensus20"
VERSION = "c22-frozen-graph-permutation-consistency-else-c12-v1"


def load(root: Path) -> dict[str, dict[str, Any]]:
    rows = {}
    for path in root.rglob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("qid"):
                rows[row["qid"]] = row
        except Exception:
            continue
    return rows


def score(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    correct = sum(row[method] == row["gold"] for row in rows)
    return {"correct": correct, "total": len(rows), "accuracy": correct / len(rows) if rows else 0.0}


def paired(rows: list[dict[str, Any]], method: str, baseline: str) -> dict[str, Any]:
    wins = sum(row[method] == row["gold"] and row[baseline] != row["gold"] for row in rows)
    losses = sum(row[method] != row["gold"] and row[baseline] == row["gold"] for row in rows)
    n = wins + losses
    tail = sum(math.comb(n, k) for k in range(0, min(wins, losses) + 1)) / (2**n) if n else 0.5
    return {"wins": wins, "losses": losses, "exact_mcnemar_p": min(1.0, 2 * tail)}


def cluster_bootstrap(rows: list[dict[str, Any]], method: str, baseline: str, iterations: int = 20_000) -> list[float]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["novel"]].append(row)
    novels = sorted(grouped)
    rng = random.Random(2208)
    points = []
    for _ in range(iterations):
        sample = [row for novel in rng.choices(novels, k=len(novels)) for row in grouped[novel]]
        delta = sum(row[method] == row["gold"] for row in sample) / len(sample) - sum(row[baseline] == row["gold"] for row in sample) / len(sample)
        points.append(100 * delta)
    points.sort()
    return [points[int(0.025 * iterations)], points[int(0.975 * iterations)]]


def main() -> None:
    tail = load(BASE / "dqa_qwen35_c15_20" / "answers" / "tail")
    graph = load(BASE / "dqa_qwen35_c15_20" / "answers" / "graph")
    perm = load(BASE / "dqa_local_c21_20" / "answers")
    c12 = load(BASE / "dqa_qwen_c12_consensus20" / "answers")
    closed = load(BASE / "dqa_qwen35_c15_20" / "answers" / "question_only")
    qids = sorted(set(tail) & set(graph) & set(perm) & set(c12) & set(closed))
    if len(qids) != 164:
        raise RuntimeError(f"expected 164 common questions, got {len(qids)}")
    rows = []
    for qid in qids:
        permuted = perm[qid].get("selected_letter")
        stable = isinstance(permuted, str) and permuted in "ABCD" and permuted == graph[qid]["selected_letter"]
        selected = graph[qid]["selected_letter"] if stable else c12[qid]["selected_letter"]
        gold = graph[qid]["gold_letter"]
        row = {
            "version": VERSION,
            "rule": "use qwen3.5 graph answer when original and reversed option orders map to the same answer; otherwise use qwen2.5 C12",
            "novel": graph[qid]["novel"], "batch": "first10" if graph[qid]["novel"] in FIRST10 else "second10",
            "qi": graph[qid]["qi"], "qid": qid, "question": graph[qid]["question"], "choices": graph[qid]["choices"],
            "gold_letter": gold, "selected_letter": selected, "correct": selected == gold,
            "route": "qwen35_permutation_stable_graph" if stable else "qwen25_c12_fallback",
            "votes": {"tail35": tail[qid]["selected_letter"], "graph35_original": graph[qid]["selected_letter"], "graph35_reversed_mapped": perm[qid].get("selected_letter"), "c12": c12[qid]["selected_letter"]},
            "models": ["qwen3.5:9b", "qwen2.5:7b-32k"], "thinking": "disabled", "external_api": False, "mask": "unmasked",
        }
        path = OUT / "answers" / row["novel"] / f"q{row['qi']:02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append({
            "novel": row["novel"], "batch": row["batch"], "qi": row["qi"], "qid": qid, "question_type": question_type(row["question"]), "gold": gold,
            "closed35": closed[qid]["selected_letter"], "tail35": tail[qid]["selected_letter"], "graph35": graph[qid]["selected_letter"], "perm35": perm[qid].get("selected_letter"), "c12": c12[qid]["selected_letter"], "c22": selected, "route": row["route"],
        })
    methods = ["closed35", "tail35", "graph35", "perm35", "c12", "c22"]
    first = [row for row in rows if row["batch"] == "first10"]
    second = [row for row in rows if row["batch"] == "second10"]
    hard = [row for row in rows if row["closed35"] != row["gold"]]
    preserved = [row for row in rows if row["closed35"] == row["gold"]]
    analysis: dict[str, Any] = {
        "metadata": {"version": VERSION, "novels": 20, "questions": 164, "external_api": False, "mask": "unmasked", "rule_frozen_on": "first10 before second10 evaluation"},
        "all": {method: score(rows, method) for method in methods},
        "first10_development": {method: score(first, method) for method in methods},
        "second10_frozen_validation": {method: score(second, method) for method in methods},
        "qwen35_question_only_wrong": {method: score(hard, method) for method in methods[1:]},
        "qwen35_question_only_correct_preservation": {method: score(preserved, method) for method in methods[1:]},
        "paired": {baseline: paired(rows, "c22", baseline) for baseline in ("tail35", "graph35", "c12")},
        "cluster_bootstrap_95_points": {baseline: cluster_bootstrap(rows, "c22", baseline) for baseline in ("tail35", "graph35", "c12")},
        "routes": {}, "by_question_type": {}, "by_novel": {},
    }
    for route in sorted({row["route"] for row in rows}):
        subset = [row for row in rows if row["route"] == route]
        analysis["routes"][route] = {method: score(subset, method) for method in ("tail35", "graph35", "perm35", "c12", "c22")}
    for kind in sorted({row["question_type"] for row in rows}):
        subset = [row for row in rows if row["question_type"] == kind]
        analysis["by_question_type"][kind] = {method: score(subset, method) for method in ("tail35", "graph35", "c12", "c22")}
    for novel in sorted({row["novel"] for row in rows}, key=int):
        subset = [row for row in rows if row["novel"] == novel]
        analysis["by_novel"][novel] = {method: score(subset, method) for method in ("tail35", "graph35", "c12", "c22")}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / "per_question.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    print(json.dumps(analysis, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
