"""Explore confidence-aware pure-graph and composite routing on DQA30.

Pure-graph calibration uses only the legacy 20-novel development cohort and is
then evaluated on the 10-novel v4 cohort. Composite routing is evaluated with
leave-one-novel-out (LONO) cross-validation on the 10-novel cohort because the
legacy cohort does not contain matching B2/B3 predictions. The latter is
exploratory and must not be presented as a frozen external validation result.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
NEW = BASE / "dqa30_attention" / "batch03_eval" / "answers"
OLD = BASE / "dqa_local_c24_pure9_consensus20" / "per_question.csv"
OLD_TYPES = BASE / "dqa_local_c16_consensus20" / "per_question.csv"
OUT = ROOT / "reports" / "DQA30_CONFIDENCE_ROUTE_SEARCH_20260824.json"
LETTERS = "ABCD"
GRAPH_OLD = ("original", "reversed", "cyclic")
GRAPH_NEW = ("G1", "G2", "G3")
ALL_NEW = ("G1", "G2", "G3", "G5", "B1", "B2", "B3")


def question_type(question: str) -> str:
    import re

    q = question.lower()
    if re.search(r"\b(not|incorrect|false|except|never|least likely|ruled out)\b", q):
        return "negative_check"
    if re.search(r"\b(who killed|killer|murderer|perpetrator|mastermind|instigator)\b", q):
        return "killer"
    if re.search(r"\b(identity|real identity|impersonat|disguise|who is|who was|whose body)\b", q):
        return "identity"
    if re.search(r"\b(why|reason|motive|purpose|cause of|caused)\b", q):
        return "motive_reason"
    if re.search(r"\b(how many|number of|symbol|letters?\s+[A-Z0-9])\b", question, re.I):
        return "quantity_symbol"
    if re.search(r"\b(where|location|room|place)\b", q):
        return "location"
    if re.search(r"\b(when|before|after|time|order)\b", q):
        return "time_order"
    if re.search(r"\b(how did|method|means|weapon|through)\b", q):
        return "method"
    return "clue_fact"


def load_old() -> list[dict[str, str]]:
    types = {}
    with OLD_TYPES.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            types[row["qid"]] = row["question_type"]
    rows = []
    with OLD.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row["qtype"] = types[row["qid"]]
            rows.append(row)
    return rows


def load_new() -> list[dict[str, Any]]:
    rows = []
    for path in sorted(NEW.rglob("q*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        answers = item["answers"]
        row = {
            "novel": str(item["novel"]),
            "qid": item["qid"],
            "question": item["question"],
            "qtype": question_type(item["question"]),
            "gold": item["gold_letter"],
            "unanimous": bool(item["attention_features"]["option_order_unanimous"]),
        }
        row.update({method: answers[method]["selected_letter"] for method in ALL_NEW})
        rows.append(row)
    return rows


def accuracy(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    correct = sum(row[key] == row["gold"] for row in rows)
    return {"correct": correct, "total": len(rows), "accuracy": correct / len(rows)}


def exact_mcnemar(rows: list[dict[str, Any]], key: str, baseline: str) -> dict[str, Any]:
    wins = sum(row[key] == row["gold"] and row[baseline] != row["gold"] for row in rows)
    losses = sum(row[key] != row["gold"] and row[baseline] == row["gold"] for row in rows)
    n = wins + losses
    tail = sum(math.comb(n, i) for i in range(min(wins, losses) + 1)) / 2**n if n else 0.5
    return {"wins": wins, "losses": losses, "exact_p": min(1.0, 2 * tail)}


def reliability(rows: list[dict[str, Any]], methods: tuple[str, ...], blend: float) -> dict[tuple[str, str], float]:
    global_scores = {}
    for method in methods:
        global_scores[method] = (sum(row[method] == row["gold"] for row in rows) + 1) / (len(rows) + 2)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["qtype"]].append(row)
    output = {}
    for qtype, subset in grouped.items():
        for method in methods:
            local = (sum(row[method] == row["gold"] for row in subset) + 2 * global_scores[method]) / (len(subset) + 2)
            output[(qtype, method)] = (1 - blend) * global_scores[method] + blend * local
    output.update({("*", method): value for method, value in global_scores.items()})
    return output


def weighted_vote(row: dict[str, Any], methods: tuple[str, ...], weights: dict[tuple[str, str], float], fallback: str) -> tuple[str, float]:
    scores = Counter()
    for method in methods:
        letter = row[method]
        scores[letter] += weights.get((row["qtype"], method), weights[("*", method)])
    ordered = scores.most_common()
    best_score = ordered[0][1]
    best = [letter for letter, value in ordered if abs(value - best_score) < 1e-12]
    selected = row[fallback] if row[fallback] in best else sorted(best)[0]
    second = ordered[1][1] if len(ordered) > 1 else 0.0
    return selected, best_score - second


def old_lono_graph(blend: float) -> tuple[float, list[str]]:
    old = load_old()
    predictions = []
    for novel in sorted({row["novel"] for row in old}, key=int):
        train = [row for row in old if row["novel"] != novel]
        test = [row for row in old if row["novel"] == novel]
        weights = reliability(train, GRAPH_OLD, blend)
        predictions.extend(weighted_vote(row, GRAPH_OLD, weights, "original")[0] for row in test)
    ordered_rows = [row for novel in sorted({row["novel"] for row in old}, key=int) for row in old if row["novel"] == novel]
    return sum(pred == row["gold"] for pred, row in zip(predictions, ordered_rows)) / len(old), predictions


def frozen_graph_predict(old: list[dict[str, Any]], new: list[dict[str, Any]], blend: float) -> list[tuple[str, float]]:
    old_mapped = []
    mapping = dict(zip(GRAPH_OLD, GRAPH_NEW))
    for row in old:
        out = {**row}
        for old_key, new_key in mapping.items():
            out[new_key] = row[old_key]
        old_mapped.append(out)
    weights = reliability(old_mapped, GRAPH_NEW, blend)
    return [weighted_vote(row, GRAPH_NEW, weights, "G1") for row in new]


def majority(row: dict[str, Any], methods: tuple[str, ...], fallback: str) -> str:
    counts = Counter(row[method] for method in methods)
    top = counts.most_common()
    best_count = top[0][1]
    best = [letter for letter, count in top if count == best_count]
    return row[fallback] if row[fallback] in best else sorted(best)[0]


def fixed_rules() -> dict[str, Callable[[dict[str, Any]], str]]:
    return {
        "G1": lambda row: row["G1"],
        "G5": lambda row: row["G5"],
        "B2_if_unanimous_else_G1": lambda row: row["B2"] if row["unanimous"] else row["G1"],
        "B3_if_unanimous_else_G1": lambda row: row["B3"] if row["unanimous"] else row["G1"],
        "G1_if_unanimous_else_B2": lambda row: row["G1"] if row["unanimous"] else row["B2"],
        "G1_if_unanimous_else_B3": lambda row: row["G1"] if row["unanimous"] else row["B3"],
        "G1_B3_agree_else_B2": lambda row: row["G1"] if row["G1"] == row["B3"] else row["B2"],
        "graph_majority_plus_B2_B3": lambda row: majority(row, ("G1", "G2", "G3", "B2", "B3"), "G1"),
        "graph_majority_plus_B2": lambda row: majority(row, ("G1", "G2", "G3", "B2"), "G1"),
        "graph_majority_plus_B3": lambda row: majority(row, ("G1", "G2", "G3", "B3"), "G1"),
    }


def nested_rule_lono(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rules = fixed_rules()
    outputs = []
    chosen = Counter()
    priority = list(rules)
    for novel in sorted({row["novel"] for row in rows}, key=int):
        train = [row for row in rows if row["novel"] != novel]
        test = [row for row in rows if row["novel"] == novel]
        ranked = sorted(
            rules,
            key=lambda name: (
                -sum(rules[name](row) == row["gold"] for row in train),
                priority.index(name),
            ),
        )
        selected_rule = ranked[0]
        chosen[selected_rule] += 1
        for row in test:
            outputs.append({**row, "nested_rule": rules[selected_rule](row), "selected_rule": selected_rule})
    return outputs, dict(chosen)


def composite_weighted_lono(rows: list[dict[str, Any]], blend: float) -> list[dict[str, Any]]:
    outputs = []
    methods = ("G1", "G2", "G3", "B2", "B3")
    for novel in sorted({row["novel"] for row in rows}, key=int):
        train = [row for row in rows if row["novel"] != novel]
        test = [row for row in rows if row["novel"] == novel]
        weights = reliability(train, methods, blend)
        for row in test:
            selected, margin = weighted_vote(row, methods, weights, "G1")
            outputs.append({**row, "weighted": selected, "confidence_margin": margin})
    return outputs


def main() -> None:
    old = load_old()
    new = load_new()
    blend_candidates = (0.0, 0.25, 0.5, 0.75, 1.0)
    old_cv = {str(blend): old_lono_graph(blend)[0] for blend in blend_candidates}
    best_blend = max(blend_candidates, key=lambda blend: (old_cv[str(blend)], -blend))
    frozen = frozen_graph_predict(old, new, best_blend)
    frozen_rows = [{**row, "frozen_graph": prediction, "graph_confidence": margin} for row, (prediction, margin) in zip(new, frozen)]

    fixed = {}
    for name, rule in fixed_rules().items():
        temp = [{**row, "candidate": rule(row)} for row in new]
        fixed[name] = {**accuracy(temp, "candidate"), "vs_B1": exact_mcnemar(temp, "candidate", "B1"), "vs_B2": exact_mcnemar(temp, "candidate", "B2"), "vs_B3": exact_mcnemar(temp, "candidate", "B3")}

    nested, chosen = nested_rule_lono(new)
    weighted_candidates = {}
    for blend in blend_candidates:
        rows = composite_weighted_lono(new, blend)
        weighted_candidates[str(blend)] = {
            **accuracy(rows, "weighted"),
            "vs_B1": exact_mcnemar(rows, "weighted", "B1"),
            "vs_B2": exact_mcnemar(rows, "weighted", "B2"),
            "vs_B3": exact_mcnemar(rows, "weighted", "B3"),
        }

    report = {
        "metadata": {
            "pure_graph_calibration": "legacy 20 novels only; evaluated on new 10 novels",
            "composite_calibration": "exploratory leave-one-novel-out on new 10 novels",
            "warning": "Rules were designed after viewing aggregate validation results and require a new holdout.",
        },
        "baselines": {method: accuracy(new, method) for method in ("B1", "B2", "B3", "G1", "G5")},
        "pure_graph": {
            "old20_lono_by_blend": old_cv,
            "selected_blend": best_blend,
            "frozen_new10": {
                **accuracy(frozen_rows, "frozen_graph"),
                "vs_B1": exact_mcnemar(frozen_rows, "frozen_graph", "B1"),
                "vs_B2": exact_mcnemar(frozen_rows, "frozen_graph", "B2"),
                "vs_B3": exact_mcnemar(frozen_rows, "frozen_graph", "B3"),
            },
        },
        "fixed_composites_posthoc": fixed,
        "nested_rule_lono": {
            **accuracy(nested, "nested_rule"),
            "chosen_rules_by_fold": chosen,
            "vs_B1": exact_mcnemar(nested, "nested_rule", "B1"),
            "vs_B2": exact_mcnemar(nested, "nested_rule", "B2"),
            "vs_B3": exact_mcnemar(nested, "nested_rule", "B3"),
        },
        "weighted_vote_lono": weighted_candidates,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
