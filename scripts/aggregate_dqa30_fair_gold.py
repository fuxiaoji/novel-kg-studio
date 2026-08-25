"""Aggregate the complete debiased gold analysis for the frozen 30-novel set.

Builds one master table (old20 / new10 / pooled) spanning:

  baselines  B1 tail, B2 compression, B3 RAG, Q0 question-only   (frozen CSV)
  control    Q0T  = Q0 under terse "Answer: X" + shuffled options (new run)
  gold       GOLD_ORIG = options-first, original order (D-anchored original)
             GOLD_V1   = evidence-first, original order
             GOLD_V2   = evidence-first, shuffled options (FAIR CEILING)
             GOLD_V3   = options-first, shuffled options
  graphs     G7, G9, G10 (frozen runs, options-first)

Also computes per-gold-letter accuracy for the de-anchored gold runs and the
Q0-terse control, and exact paired McNemar tests on the key contrasts.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from aggregate_dqa30_pure_graph_results import mcnemar  # noqa: E402

OUT = ROOT / "paper" / "generated" / "dqa30_fair_gold_results.json"
BASE = ROOT / "outputs" / "four_datasets" / "dqa30_attention"

METHODS = ("B1", "B2", "B3", "Q0", "Q0T", "GOLD_ORIG", "GOLD_V1", "GOLD_V2", "GOLD_V3", "G7", "G9", "G10")
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
    gold_orig = load_json_dir(BASE / "goldonly_9b_30" / "answers")
    gold_v1 = load_json_dir(BASE / "goldonly_9b_30_fair" / "v1_evid_first" / "answers")
    gold_v2 = load_json_dir(BASE / "goldonly_9b_30_fair" / "v2_evid_first_shuf" / "answers")
    gold_v3 = load_json_dir(BASE / "goldonly_9b_30_fair" / "v3_opt_first_shuf" / "answers")
    q0t = load_json_dir(BASE / "q0_terse_30" / "answers")

    rows: list[dict[str, Any]] = []
    missing: dict[str, int] = defaultdict(int)
    for row in frozen:
        qid, novel, qi = row["qid"], row["novel"], row["qi"]
        gold_letter = row["gold"]
        cohort = "old20" if novel in OLD20 else "new10"
        correct: dict[str, bool] = {}

        def set_correct(name: str, item: dict[str, Any] | None) -> None:
            if item is not None:
                # For shuffled-option runs the selected letter lives in the shown order;
                # trust the stored `correct` field (== letter == LETTERS[shown_gold]).
                stored = item.get("correct")
                correct[name] = bool(stored) if stored is not None else item.get("selected_letter") == gold_letter
            else:
                missing[name] += 1

        set_correct("G7", g7.get(qid))
        g9 = g9_old if cohort == "old20" else g9_new
        g10 = g10_old if cohort == "old20" else g10_new
        set_correct("G9", g9.get(qid))
        set_correct("G10", g10.get(qid))
        set_correct("GOLD_ORIG", gold_orig.get(qid))
        set_correct("GOLD_V1", gold_v1.get(qid))
        set_correct("GOLD_V2", gold_v2.get(qid))
        set_correct("GOLD_V3", gold_v3.get(qid))
        set_correct("Q0T", q0t.get(qid))
        for m in ("B1", "B2", "B3", "Q0"):
            correct[m] = row[m] == gold_letter
        rows.append({"cohort": cohort, "novel": novel, "qi": qi, "qid": qid, "gold": gold_letter, "correct": correct})

    for method, count in missing.items():
        print(f"WARNING: missing {method} answers for {count} questions")

    def score(subset: list[dict[str, Any]], m: str) -> dict[str, Any]:
        n = sum(1 for r in subset if m in r["correct"])
        k = sum(1 for r in subset if r["correct"].get(m))
        return {"n": n, "correct": k, "micro": k / n if n else None}

    def block(subset: list[dict[str, Any]]) -> dict[str, Any]:
        return {m: score(subset, m) for m in METHODS}

    # Per-gold-letter accuracy (only for letter-shuffled / evidence-carrying runs with shown order)
    def per_letter(items: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        acc: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for item in items.values():
            letter = item.get("gold_letter")
            if not letter:
                continue
            if isinstance(item.get("selected_letter"), str):
                acc[letter][1] += 1
                acc[letter][0] += int(bool(item.get("correct")))
        return {k: {"correct": v[0], "n": v[1], "acc": v[0] / v[1] if v[1] else None} for k, v in sorted(acc.items())}

    # Selected-letter distribution (de-anchor check)
    def sel_dist(items: dict[str, dict[str, Any]]) -> dict[str, int]:
        return dict(Counter(item.get("selected_letter") for item in items.values() if isinstance(item.get("selected_letter"), str)))

    report: dict[str, Any] = {
        "metadata": {
            "protocol": "dqa30-fair-gold-v1",
            "answer_model": "qwen3.5:9b",
            "thinking": "disabled",
            "question_set": "frozen 30-novel 234 q",
            "gold_definition": "question + options + every nonnegative official clue_position paragraph; no final-answer paragraph",
            "fair_protocol": "evidence-before-options + per-qid sha256-seeded option shuffle + terse 'Answer: [letter]'",
            "old20": f"{len(OLD20)} legacy graphs", "new10": "10 Pass2-v4 graphs",
            "warning": "pooled 30 is descriptive (heterogeneous graph cohorts)",
        },
        "old20": block([r for r in rows if r["cohort"] == "old20"]),
        "new10": block([r for r in rows if r["cohort"] == "new10"]),
        "pooled": block(rows),
        "per_gold_letter": {
            "GOLD_V2": per_letter(gold_v2),
            "GOLD_V3": per_letter(gold_v3),
            "Q0T": per_letter(q0t),
            "GOLD_ORIG": per_letter(gold_orig),
        },
        "selected_letter_dist": {"GOLD_V2": sel_dist(gold_v2), "GOLD_V3": sel_dist(gold_v3), "Q0T": sel_dist(q0t)},
        "paired": {
            # Gold evidence vs no evidence, de-anchored protocol
            "GOLD_V2_vs_Q0T": mcnemar(rows, "GOLD_V2", "Q0T"),
            # Structure effect: evidence-first vs options-first (both shuffled)
            "GOLD_V2_vs_GOLD_V3": mcnemar(rows, "GOLD_V2", "GOLD_V3"),
            # Perfect-retrieval gold ceiling vs graph methods
            "GOLD_V2_vs_G7": mcnemar(rows, "GOLD_V2", "G7"),
            "GOLD_V2_vs_G10": mcnemar(rows, "GOLD_V2", "G10"),
            # Original D-anchored gold vs Q0 (should be ~null)
            "GOLD_ORIG_vs_Q0": mcnemar(rows, "GOLD_ORIG", "Q0"),
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("== master table (micro accuracy) ==")
    print(f"{'method':<12}" + "".join(f"{c:>8}" for c in ("old20", "new10", "pooled")))
    for m in METHODS:
        vals = [report["old20"][m]["micro"], report["new10"][m]["micro"], report["pooled"][m]["micro"]]
        s = f"{m:<12}" + "".join(f"{(v * 100 if v else float('nan')):6.1f}%  " for v in vals)
        print(s)
    print("\n== paired McNemar (pooled, exact two-sided) ==")
    for key, p in report["paired"].items():
        print(f"{key:<22} delta={p['delta']:+.2f}  wins={p['wins']}  losses={p['losses']}  exact_p={p['exact_p']:.4f}")


if __name__ == "__main__":
    main()
