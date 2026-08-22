"""Analyze equal-budget C9 tail/hybrid results, including prior-stripped sets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_c8_metrics import load_json_answers, macro_by_novel, paired_gain, summarize  # noqa: E402
from analyze_c_combined20 import load_rows  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, SECOND10, answer_path as c8_path  # noqa: E402
from run_c9_20 import answer_path as c9_path  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=BASE / "dqa_qwen_c9_20")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    old = {(row["novel"], int(row["qi"])): row for row in load_rows()}
    qwen = load_json_answers(BASE / "dqa_qwen_question_only20" / "answers")
    deepseek = load_json_answers(BASE / "dqa_deepseek_v4flash_full_vs_question20_nothinking" / "answers" / "question_only")
    compression = load_json_answers(BASE / "dqa_qwen_compress20" / "answers")
    rows = []
    for novel in NOVELS:
        for qi in sorted(index for n, index in old if n == novel):
            paths = {method: c9_path(args.root, method, novel, qi) for method in ("tail50", "hybrid")}
            if not all(path.exists() for path in paths.values()):
                if args.allow_partial:
                    continue
                raise FileNotFoundError(paths)
            answers = {method: json.loads(path.read_text(encoding="utf-8")) for method, path in paths.items()}
            prior = old[(novel, qi)]
            qid = prior["qid"]
            gold = prior["gold_letter"]
            row: dict[str, Any] = {
                "novel": novel, "qi": qi, "qid": qid, "batch": prior["batch"],
                "question_type": prior["question_type"], "qwen_closed": qwen[qid]["correct"],
                "deepseek_closed": deepseek[qid]["correct"], "tail_old": prior["tail_unmasked"] == gold,
                "c4": prior["c4_unmasked"] == gold, "compress": bool(compression.get(qid, {}).get("correct")),
            }
            for method, answer in answers.items():
                row[method] = answer.get("selected_letter") == gold
                row[f"{method}_chars"] = sum(len(chunk["text"]) for chunk in answer["retrieval"]["chunks"])
            c8_graph = c8_path(BASE / "dqa_qwen_c8_20", "graph", novel, qi)
            if c8_graph.exists():
                graph_answer = json.loads(c8_graph.read_text(encoding="utf-8"))
                row["graph"] = graph_answer.get("selected_letter") == gold
                row["killer_gate"] = row["graph"] if row["question_type"] == "killer" else row["tail_old"]
            else:
                row["graph"] = False
                row["killer_gate"] = row["tail_old"]
            rows.append(row)

    sets = {
        "all": rows,
        "qwen_hard": [row for row in rows if not row["qwen_closed"]],
        "conservative_hard": [row for row in rows if not row["qwen_closed"] and not row["deepseek_closed"]],
    }
    methods = ("tail_old", "compress", "c4", "graph", "killer_gate", "tail50", "hybrid")
    report: dict[str, Any] = {
        "definitions": {
            "tail50": "matched prompt with 34 terminal source chunks",
            "hybrid": "same 34-chunk budget: 24 terminal chunks plus 10 whole-book retrieval chunks and strictly grounded graph hints",
            "conservative_hard": "both closed-book models incorrect",
        },
        "counts": {name: len(subset) for name, subset in sets.items()}, "sets": {}, "by_batch": {},
    }
    for name, subset in sets.items():
        block = {method: {**summarize(subset, method), **macro_by_novel(subset, method)} for method in methods}
        for baseline in ("tail50", "tail_old", "compress", "c4", "graph"):
            block["hybrid"][f"vs_{baseline}"] = paired_gain(subset, "hybrid", baseline)
        report["sets"][name] = block
    closed_wrong = sets["qwen_hard"]
    closed_correct = [row for row in rows if row["qwen_closed"]]
    for method in methods:
        rescue = sum(row[method] for row in closed_wrong)
        preserved = sum(row[method] for row in closed_correct)
        report["sets"]["all"][method].update({
            "evidence_rescue_rate": rescue / len(closed_wrong) if closed_wrong else 0.0,
            "prior_preservation_rate": preserved / len(closed_correct) if closed_correct else 0.0,
            "net_novel_gain": (rescue - (len(closed_correct) - preserved)) / len(rows) if rows else 0.0,
        })
    for batch, novels in (("first10", FIRST10), ("second10", SECOND10)):
        subset = [row for row in rows if row["novel"] in novels]
        report["by_batch"][batch] = {method: summarize(subset, method) for method in methods}
    report["diagnostics"] = {
        "equal_character_budget_questions": sum(row["tail50_chars"] == row["hybrid_chars"] for row in rows),
        "questions": len(rows),
        "average_tail50_chars": sum(row["tail50_chars"] for row in rows) / len(rows) if rows else 0.0,
        "average_hybrid_chars": sum(row["hybrid_chars"] for row in rows) / len(rows) if rows else 0.0,
    }
    args.root.mkdir(parents=True, exist_ok=True)
    (args.root / "analysis_metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
