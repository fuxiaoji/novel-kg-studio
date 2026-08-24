"""Audit G6 single-graph retrieval and confidence-aware composites on frozen DQA new10."""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets" / "dqa30_attention"
BASELINE = BASE / "batch03_eval" / "answers"
G6_ROOT = BASE / "g6_graph_expansion" / "answers"
OUT = ROOT / "reports" / "DQA30_G6_BREAKTHROUGH_ANALYSIS_20260824.json"
LETTERS = "ABCD"


def qtype(text: str) -> str:
    q = text.lower()
    if re.search(r"\b(not|incorrect|false|except|never|least likely|ruled out|couldn't possibly)\b", q):
        return "negative"
    if re.search(r"\b(who killed|killer|murderer|perpetrator|mastermind|instigator)\b", q):
        return "killer"
    if re.search(r"\b(identity|real identity|impersonat|disguise|who is|who was|whose body)\b", q):
        return "identity"
    if re.search(r"\b(why|reason|motive|purpose|cause)\b", q):
        return "reason"
    return "fact"


def load_rows() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(G6_ROOT.rglob("q*.json")):
        g6 = json.loads(path.read_text(encoding="utf-8"))
        baseline_path = BASELINE / str(g6["novel"]) / path.name
        base = json.loads(baseline_path.read_text(encoding="utf-8"))
        answers = base["answers"]
        features = g6["confidence_features"]
        row = {
            "novel": str(g6["novel"]),
            "qid": g6["qid"],
            "question": g6["question"],
            "gold": g6["gold_letter"],
            "qtype": qtype(g6["question"]),
            "G6": g6["selected_letter"],
            "g6_confidence": str(g6.get("confidence") or "low").lower(),
            "valid_relations": int(features["valid_relation_count"]),
            "removed_relations": int(features["removed_relation_count"]),
            "graph_only_chunks": int(features["graph_only_chunks"]),
            "rag_overlap": float(features["rag_overlap"]),
            "graph_margin": float(features["graph_margin"]),
            "unanimous": bool(base["attention_features"]["option_order_unanimous"]),
        }
        for method in ("G1", "G2", "G3", "G5", "B1", "B2", "B3", "Q0"):
            row[method] = answers[method]["selected_letter"]
        rows.append(row)
    return rows


def metric(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    n = len(rows)
    correct = sum(row[key] == row["gold"] for row in rows)
    if not n:
        return {"correct": 0, "total": 0, "accuracy": None, "wilson95": [None, None]}
    p = correct / n
    z = 1.959963984540054
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {"correct": correct, "total": n, "accuracy": p, "wilson95": [center - half, center + half]}


def mcnemar(rows: list[dict[str, Any]], key: str, baseline: str) -> dict[str, Any]:
    wins = sum(row[key] == row["gold"] and row[baseline] != row["gold"] for row in rows)
    losses = sum(row[key] != row["gold"] and row[baseline] == row["gold"] for row in rows)
    n = wins + losses
    if not n:
        p = 1.0
    else:
        p = min(1.0, 2 * sum(math.comb(n, i) for i in range(min(wins, losses) + 1)) / 2**n)
    return {"wins": wins, "losses": losses, "exact_p": p}


def cluster_bootstrap_delta(rows: list[dict[str, Any]], key: str, baseline: str, seed: int = 20260824) -> dict[str, Any]:
    rng = random.Random(seed)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["novel"]].append(row)
    novels = sorted(grouped, key=int)
    values = []
    for _ in range(20000):
        sample = [row for _novel in rng.choices(novels, k=len(novels)) for row in grouped[_novel]]
        values.append(sum((row[key] == row["gold"]) - (row[baseline] == row["gold"]) for row in sample) / len(sample))
    values.sort()
    delta = metric(rows, key)["accuracy"] - metric(rows, baseline)["accuracy"]
    return {"delta": delta, "cluster_bootstrap95": [values[499], values[19499]]}


def rules() -> dict[str, Callable[[dict[str, Any]], str]]:
    return {
        "G6": lambda r: r["G6"],
        "G6_high_else_B2": lambda r: r["G6"] if r["g6_confidence"] == "high" else r["B2"],
        "G6_high_else_B3": lambda r: r["G6"] if r["g6_confidence"] == "high" else r["B3"],
        "G6_agrees_G1_else_B2": lambda r: r["G6"] if r["G6"] == r["G1"] else r["B2"],
        "G6_agrees_G5_else_B2": lambda r: r["G6"] if r["G6"] == r["G5"] else r["B2"],
        "G6_agrees_any_graph_else_B2": lambda r: r["G6"] if r["G6"] in {r["G1"], r["G3"], r["G5"]} else r["B2"],
        "G6_agrees_two_graph_else_B2": lambda r: r["G6"] if sum(r["G6"] == r[m] for m in ("G1", "G3", "G5")) >= 2 else r["B2"],
        "B2_if_unanimous_else_G6": lambda r: r["B2"] if r["unanimous"] else r["G6"],
        "G6_if_unanimous_else_B2": lambda r: r["G6"] if r["unanimous"] else r["B2"],
        "G6_if_graph_only_ge3_else_B2": lambda r: r["G6"] if r["graph_only_chunks"] >= 3 else r["B2"],
        "G6_if_overlap_le50_else_B2": lambda r: r["G6"] if r["rag_overlap"] <= 0.5 else r["B2"],
        "G6_if_relations_ge6_else_B2": lambda r: r["G6"] if r["valid_relations"] >= 6 else r["B2"],
    }


def apply_rule(rows: list[dict[str, Any]], name: str, fn: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    return [{**row, name: fn(row)} for row in rows]


def nested_rule_lono(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidates = rules()
    priority = list(candidates)
    output = []
    selected = Counter()
    for novel in sorted({row["novel"] for row in rows}, key=int):
        train = [row for row in rows if row["novel"] != novel]
        test = [row for row in rows if row["novel"] == novel]
        best = min(
            candidates,
            key=lambda name: (-sum(candidates[name](r) == r["gold"] for r in train), priority.index(name)),
        )
        selected[best] += 1
        output.extend({**row, "LONO": candidates[best](row), "selected_rule": best} for row in test)
    return output, dict(selected)


def confidence_vote_lono(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    methods = ("G6", "G1", "G3", "G5", "B2", "B3")
    output = []
    for novel in sorted({row["novel"] for row in rows}, key=int):
        train = [row for row in rows if row["novel"] != novel]
        test = [row for row in rows if row["novel"] == novel]
        weights = {m: (sum(r[m] == r["gold"] for r in train) + 1) / (len(train) + 2) for m in methods}
        g6_high = [r for r in train if r["g6_confidence"] == "high"]
        high_rel = (sum(r["G6"] == r["gold"] for r in g6_high) + 1) / (len(g6_high) + 2)
        for row in test:
            scores = Counter()
            for method in methods:
                weight = high_rel if method == "G6" and row["g6_confidence"] == "high" else weights[method]
                scores[row[method]] += weight
            ordered = scores.most_common()
            best_score = ordered[0][1]
            tied = sorted(letter for letter, score in ordered if abs(score - best_score) < 1e-12)
            prediction = row["G6"] if row["G6"] in tied else tied[0]
            second = ordered[1][1] if len(ordered) > 1 else 0.0
            output.append({**row, "CONF_VOTE": prediction, "confidence_margin": best_score - second})
    return output


def compare(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return {
        **metric(rows, key),
        "vs_B1": {**mcnemar(rows, key, "B1"), **cluster_bootstrap_delta(rows, key, "B1")},
        "vs_B2": {**mcnemar(rows, key, "B2"), **cluster_bootstrap_delta(rows, key, "B2")},
        "vs_B3": {**mcnemar(rows, key, "B3"), **cluster_bootstrap_delta(rows, key, "B3")},
    }


def main() -> None:
    rows = load_rows()
    hard = [row for row in rows if row["Q0"] != row["gold"]]
    fixed = {}
    fixed_rows = {}
    for name, fn in rules().items():
        evaluated = apply_rule(rows, name, fn)
        fixed_rows[name] = evaluated
        fixed[name] = compare(evaluated, name)
    lono, selected = nested_rule_lono(rows)
    vote = confidence_vote_lono(rows)
    per_novel = {}
    for novel in sorted({row["novel"] for row in rows}, key=int):
        subset = [row for row in rows if row["novel"] == novel]
        per_novel[novel] = {method: metric(subset, method) for method in ("G6", "B1", "B2", "B3")}
    report = {
        "metadata": {
            "model": "qwen3.5:9b; thinking disabled; forced four-choice",
            "cohort": "DQA new10 v4, 70 questions",
            "single_method": "G6 graph-guided source expansion with grounded relation filtering",
            "warning": "G6 and confidence rules were developed after prior aggregate inspection; results are development-set evidence, not untouched confirmation.",
        },
        "baselines": {m: metric(rows, m) for m in ("Q0", "B1", "B2", "B3", "G1", "G5")},
        "G6_single": compare(rows, "G6"),
        "hard_q0_wrong": {m: metric(hard, m) for m in ("G6", "B1", "B2", "B3", "G1", "G5")},
        "per_novel": per_novel,
        "fixed_confidence_rules_posthoc": fixed,
        "nested_rule_lono": {**compare(lono, "LONO"), "selected_rules_by_fold": selected},
        "confidence_weighted_vote_lono": compare(vote, "CONF_VOTE"),
        "oracle": {
            "G6_B2_B3": sum(any(row[m] == row["gold"] for m in ("G6", "B2", "B3")) for row in rows) / len(rows),
            "G6_G1_G5_B2_B3": sum(any(row[m] == row["gold"] for m in ("G6", "G1", "G5", "B2", "B3")) for row in rows) / len(rows),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
