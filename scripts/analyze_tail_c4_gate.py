"""Audit observable C4 evidence features for a leakage-safe tail/C4 gate."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_question_only20 import BASE, GRAPH_ALL, MASKS, is_correct, load, mcnemar

OUT = BASE / "dqa_qwen_question_only20"


def rich_c4(row: dict[str, Any], mask: str) -> dict[str, Any]:
    root = BASE / ("dqa_qwen_c_improvements" if row["batch"] == "first10" else "dqa_qwen_c_next10_methods")
    path = root / "c4" / mask / row["novel"] / f"q{int(row['qi']):02d}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def features(row: dict[str, Any], mask: str) -> dict[str, Any]:
    data = rich_c4(row, mask)
    c4 = row[f"c4_{mask}"]
    evidence = data.get("evidence", {}) if isinstance(data.get("evidence"), dict) else {}
    selected = evidence.get(c4, {}) if isinstance(evidence.get(c4), dict) else {}
    support = selected.get("support", []) if isinstance(selected.get("support"), list) else []
    contradict = selected.get("contradict", []) if isinstance(selected.get("contradict"), list) else []
    ids = data.get("evidence_ids", []) if isinstance(data.get("evidence_ids"), list) else []
    agree = sum(row[f"{method}_{mask}"] == c4 for method in GRAPH_ALL if method != "c4")
    confidence = {"low": 0, "medium": 1, "high": 2}.get(str(data.get("confidence", "low")).lower(), 0)
    return {
        "evidence_count": len(set(str(x) for x in ids)),
        "support_count": len(support),
        "contradict_count": len(contradict),
        "graph_agree": agree,
        "c6_agrees": row[f"c6_{mask}"] == c4,
        "confidence": confidence,
        "fallback_used": bool(data.get("fallback_used")),
    }


def choose(row: dict[str, Any], mask: str, params: tuple[int, int, int, int, bool]) -> str:
    evidence_min, support_min, agree_min, confidence_min, require_c6 = params
    c4, tail = row[f"c4_{mask}"], row[f"tail_{mask}"]
    if c4 == tail:
        return tail
    f = row[f"gate_features_{mask}"]
    use_c4 = (
        f["evidence_count"] >= evidence_min
        and f["support_count"] >= support_min
        and f["graph_agree"] >= agree_min
        and f["confidence"] >= confidence_min
        and (not require_c6 or f["c6_agrees"])
    )
    return c4 if use_c4 else tail


def accuracy(rows: list[dict[str, Any]], mask: str, params: tuple[int, int, int, int, bool]) -> tuple[int, int]:
    correct = sum(choose(row, mask, params) == row["gold_letter"] for row in rows)
    switches = sum(choose(row, mask, params) == row[f"c4_{mask}"] and row[f"c4_{mask}"] != row[f"tail_{mask}"] for row in rows)
    return correct, switches


def loo_gate(all_rows: list[dict[str, Any]], mask: str) -> list[str]:
    grid = list(product(range(0, 7), range(0, 4), range(0, 5), range(0, 3), (False, True)))
    predictions = []
    for row in all_rows:
        train = [r for r in all_rows if r["novel"] != row["novel"]]
        scored = [(accuracy(train, mask, params), params) for params in grid]
        # Prefer accuracy, then fewer switches, then stricter evidence gates.
        _, best = max(scored, key=lambda item: (item[0][0], -item[0][1], sum(item[1][:4]), int(item[1][4])))
        predictions.append(choose(row, mask, best))
    return predictions


def summarize_feature_groups(rows: list[dict[str, Any]], mask: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row[f"tail_{mask}"] == row[f"c4_{mask}"]:
            label = "agree"
        elif is_correct(row, "c4", mask) and not is_correct(row, "tail", mask):
            label = "c4_win"
        elif is_correct(row, "tail", mask) and not is_correct(row, "c4", mask):
            label = "tail_win"
        else:
            label = "both_wrong"
        groups[label].append(row[f"gate_features_{mask}"])
    result = {}
    for label, values in groups.items():
        numeric = ("evidence_count", "support_count", "contradict_count", "graph_agree", "confidence")
        result[label] = {"n": len(values), **{key: sum(v[key] for v in values) / len(values) for key in numeric}, "c6_agree_rate": sum(v["c6_agrees"] for v in values) / len(values)}
    return result


def evaluate(predictions: list[str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    correct = sum(p == r["gold_letter"] for p, r in zip(predictions, rows))
    return {"correct": correct, "total": len(rows), "accuracy": correct / len(rows)}


def paired(predictions: list[str], rows: list[dict[str, Any]], mask: str) -> dict[str, Any]:
    return {
        "vs_tail": mcnemar(predictions, [r[f"tail_{mask}"] for r in rows], rows),
        "vs_c4": mcnemar(predictions, [r[f"c4_{mask}"] for r in rows], rows),
    }

def main() -> None:
    rows = load()
    hard = [r for r in rows if not r["qonly_correct"]]
    report: dict[str, Any] = {}
    for mask in MASKS:
        for row in rows:
            row[f"gate_features_{mask}"] = features(row, mask)
        loo = loo_gate(rows, mask)
        hard_ids = {r["qid"] for r in hard}
        loo_hard = [p for p, r in zip(loo, rows) if r["qid"] in hard_ids]
        fixed = {
            "evidence2_agree1": (2, 1, 1, 0, False),
            "evidence1_agree2": (1, 1, 2, 0, False),
            "c6_and_evidence": (1, 1, 0, 0, True),
            "strict": (2, 1, 2, 1, True),
        }
        report[mask] = {
            "feature_groups_full": summarize_feature_groups(rows, mask),
            "feature_groups_hard": summarize_feature_groups(hard, mask),
            "loo_gate_full": evaluate(loo, rows),
            "loo_gate_hard": evaluate(loo_hard, hard),
            "loo_gate_full_paired": paired(loo, rows, mask),
            "loo_gate_hard_paired": paired(loo_hard, hard, mask),
            "fixed_full": {name: evaluate([choose(r, mask, params) for r in rows], rows) for name, params in fixed.items()},
            "fixed_hard": {name: evaluate([choose(r, mask, params) for r in hard], hard) for name, params in fixed.items()},
            "tail_c4_oracle_full": evaluate([r[f"c4_{mask}"] if is_correct(r, "c4", mask) else r[f"tail_{mask}"] for r in rows], rows),
            "tail_c4_oracle_hard": evaluate([r[f"c4_{mask}"] if is_correct(r, "c4", mask) else r[f"tail_{mask}"] for r in hard], hard),
        }
    (OUT / "tail_c4_gate_analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
