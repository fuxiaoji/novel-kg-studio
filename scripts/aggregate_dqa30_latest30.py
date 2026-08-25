"""Aggregate the latest 30-novel method table: G7 / G9 / G10 / gold baseline / B1-B3 / Q0.

Sources (all same protocol qwen3.5:9b unless noted):
  G7  -> outputs/.../g7_pure_graph_tight/answers   (30 novels, 234 q)
  G9  -> outputs/.../g9_graph_rerank_old20 (old20) + g9_graph_rerank_weak18 (new10)
  G10 -> outputs/.../g10_graph_referee_old20 (old20) + g10_graph_referee_new10 (new10)
  Gold-> outputs/.../goldonly_9b_30/answers         (30 novels, 234 q)
  B1/B2/B3/Q0 -> paper/generated/dqa30_per_question.csv (frozen 9B baselines)

All methods share the same 234 frozen questions.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aggregate_dqa30_pure_graph_results import bootstrap, mcnemar, score, wilson  # noqa: E402

OUT = ROOT / "paper" / "generated" / "dqa30_latest30_results.json"
BASE = ROOT / "outputs" / "four_datasets" / "dqa30_attention"
METHODS = ("G7", "G9", "G10", "GOLD", "B1", "B2", "B3", "Q0")
GRAPHS = ("G7", "G9", "G10")
BASELINES = ("B1", "B2", "B3")
OLD20 = ["26", "27", "28", "30", "31", "33", "40", "53", "56", "79", "15", "16", "25", "29", "81", "82", "83", "84", "87", "90"]


def load_json_dir(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.rglob("q*.json")):
        item = json.loads(path.read_text(encoding="utf-8"))
        if item.get("qid"):
            result[item["qid"]] = item
    return result


def main() -> None:
    frozen = list(csv.DictReader((ROOT / "paper" / "generated" / "dqa30_per_question.csv").open(encoding="utf-8-sig", newline="")))
    g7 = load_json_dir(BASE / "g7_pure_graph_tight" / "answers")
    g9_old = load_json_dir(BASE / "g9_graph_rerank_old20" / "answers")
    g9_new = load_json_dir(BASE / "g9_graph_rerank_weak18" / "answers")
    g10_old = load_json_dir(BASE / "g10_graph_referee_old20" / "answers")
    g10_new = load_json_dir(BASE / "g10_graph_referee_new10" / "answers")
    gold = load_json_dir(BASE / "goldonly_9b_30" / "answers")

    rows: list[dict[str, Any]] = []
    missing: dict[str, int] = defaultdict(int)
    for row in frozen:
        qid, novel, qi = row["qid"], row["novel"], row["qi"]
        gold_letter = row["gold"]
        cohort = "old20" if novel in OLD20 else "new10"
        correct: dict[str, bool] = {}
        # G7
        if qid in g7:
            correct["G7"] = g7[qid].get("selected_letter") == gold_letter
        else:
            missing["G7"] += 1
        # G9 / G10
        g9 = g9_old if cohort == "old20" else g9_new
        g10 = g10_old if cohort == "old20" else g10_new
        if qid in g9:
            correct["G9"] = g9[qid].get("selected_letter") == gold_letter
        else:
            missing["G9"] += 1
        if qid in g10:
            correct["G10"] = g10[qid].get("selected_letter") == gold_letter
        else:
            missing["G10"] += 1
        # Gold 9B
        if qid in gold:
            correct["GOLD"] = gold[qid].get("selected_letter") == gold_letter
        else:
            missing["GOLD"] += 1
        # Baselines from frozen per-question CSV
        for m in ("B1", "B2", "B3", "Q0"):
            correct[m] = row[m] == gold_letter
        rows.append({"cohort": cohort, "novel": novel, "qi": qi, "qid": qid, "gold": gold_letter, "correct": correct})

    for method, count in missing.items():
        print(f"WARNING: missing {method} answers for {count} questions")

    def cohort_stats(subset: list[dict[str, Any]]) -> dict[str, Any]:
        hard = [r for r in subset if not r["correct"]["Q0"]]
        easy = [r for r in subset if r["correct"]["Q0"]]
        pairs = {f"{g}_vs_{b}": mcnemar(subset, g, b) for g in GRAPHS for b in BASELINES}
        ordered = sorted(pairs, key=lambda key: pairs[key]["exact_p"])
        running = 0.0
        for rank, key in enumerate(ordered):
            running = max(running, min(1.0, pairs[key]["exact_p"] * (len(ordered) - rank)))
            pairs[key]["holm_p"] = running
        return {
            "questions": len(subset),
            "novels": len({r["novel"] for r in subset}),
            "all": {m: score(subset, m) for m in METHODS},
            "q0_wrong": {m: score(hard, m) for m in METHODS if m != "Q0"},
            "q0_correct_preservation": {m: score(easy, m) for m in METHODS if m != "Q0"},
            "paired_graph_vs_baselines": pairs,
        }

    report = {
        "metadata": {
            "protocol": "dqa30-latest-g7-g9-g10-v1",
            "single_answer_model": "qwen3.5:9b",
            "thinking": "disabled",
            "mask": "unmasked",
            "graph_methods": {
                "G7": "tight graph expansion; baseline_access=false; frozen 234-row run",
                "G9": "graph-native metadata rerank 28->8 chunks",
                "G10": "graph-only disagreement referee over G7+G9",
                "GOLD": "official clue_position paragraphs as only evidence (perfect-retrieval ceiling)",
            },
            "gold_baseline_note": "Gold = qwen3.5:9b, question + options + every nonnegative official clue_position paragraph; no final-answer paragraph.",
            "warning": "old20 and new10 use different graph-build versions; pooled30 is descriptive",
        },
        "old20": cohort_stats([r for r in rows if r["cohort"] == "old20"]),
        "new10": cohort_stats([r for r in rows if r["cohort"] == "new10"]),
        "descriptive30": cohort_stats(rows),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: {m: block["all"][m]["micro_accuracy"] for m in METHODS} for name, block in report.items() if name in ("old20", "new10", "descriptive30")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
