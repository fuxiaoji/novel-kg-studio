"""Exact-letter and paired analysis for the next option-C round."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_c_improvements import all_questions, bootstrap_delta, correct, holm, mcnemar_exact, old_answer
from c_option_methods import LETTERS, normalize_letter

RUN_METHODS = ["c4fix", "c1perm", "c3disc", "gate"]
NEXT_METHODS = [*RUN_METHODS, "c3stage1"]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def new_path(root: Path, method: str, mask: str, novel: str, qi: int) -> Path:
    return root / method / mask / novel / f"q{qi:02d}.json"


def letters_for(rows: list[dict[str, Any]], method: str, mask: str, graph_root: Path, next_root: Path, prior_root: Path) -> list[str | None]:
    values = []
    for row in rows:
        if method == "old_tail":
            value, _ = old_answer(graph_root, row["novel"], row["qi"], "tail", mask)
        elif method == "old_c3":
            value, _ = old_answer(graph_root, row["novel"], row["qi"], "v5b", mask)
        elif method == "prior_c6":
            value = normalize_letter(load_json(new_path(prior_root, "c6", mask, row["novel"], row["qi"])).get("selected_letter"))
        else:
            value = normalize_letter(load_json(new_path(next_root, method, mask, row["novel"], row["qi"])).get("selected_letter"))
        values.append(value)
    return values


def diagnostics(next_root: Path) -> dict[str, Any]:
    result = {}
    for method in NEXT_METHODS:
        for mask in ("masked", "unmasked"):
            rows = [load_json(p) for p in sorted((next_root / method / mask).glob("*/*.json"))]
            elapsed = [r.get("elapsed_seconds") for r in rows if isinstance(r.get("elapsed_seconds"), (int, float))]
            result[f"{method}/{mask}"] = {
                "answers": len(rows),
                "unparsed": sum(normalize_letter(r.get("selected_letter")) is None for r in rows),
                "average_elapsed_seconds": statistics.mean(elapsed) if elapsed else None,
                "grounded_answers": sum(bool(r.get("evidence_ids")) for r in rows),
                "extraction_fallbacks": sum(bool(r.get("fallback_used")) for r in rows),
                "permutation_consistent": sum(bool(r.get("permutation_consistent")) for r in rows),
                "stage_counts": dict(Counter(str(r.get("stopped_after_stage")) for r in rows)),
                "gated_direct": sum(bool(r.get("gated_direct")) for r in rows),
                "prediction_distribution": dict(Counter(normalize_letter(r.get("selected_letter")) or "unparsed" for r in rows)),
            }
    return result


def evaluate(rows: list[dict[str, Any]], graph_root: Path, next_root: Path, prior_root: Path) -> dict[str, Any]:
    methods = ["old_tail", "old_c3", "prior_c6", *NEXT_METHODS]
    report: dict[str, Any] = {"masks": {}, "diagnostics": diagnostics(next_root)}
    for mask in ("masked", "unmasked"):
        letters = {m: letters_for(rows, m, mask, graph_root, next_root, prior_root) for m in methods}
        correctness = {m: [correct(x, row) for x, row in zip(letters[m], rows)] for m in methods}
        summary = {
            m: {
                "correct": sum(correctness[m]),
                "total": len(rows),
                "accuracy": sum(correctness[m]) / len(rows),
                "parsed": sum(x is not None for x in letters[m]),
            }
            for m in methods
        }
        comparisons = []
        for method in ["prior_c6", *NEXT_METHODS]:
            base = "old_c3" if method in {"c3disc", "c3stage1"} else "old_tail"
            stat = mcnemar_exact(correctness[method], correctness[base])
            delta = summary[method]["accuracy"] - summary[base]["accuracy"]
            comparisons.append(
                {
                    "method": method,
                    "baseline": base,
                    "delta": delta,
                    "bootstrap_95": bootstrap_delta(rows, correctness[method], correctness[base]),
                    "practical_gain": delta >= 0.05,
                    "mcnemar": stat,
                }
            )
        holm(comparisons)
        for comp in comparisons:
            comp["significant_and_practical"] = comp["practical_gain"] and comp["mcnemar"]["p_holm"] < 0.05
        by_type = defaultdict(dict)
        by_novel = defaultdict(dict)
        for qtype in sorted({r["question_type"] for r in rows}):
            ids = [i for i, r in enumerate(rows) if r["question_type"] == qtype]
            for method in methods:
                by_type[qtype][method] = {"correct": sum(correctness[method][i] for i in ids), "total": len(ids)}
        for novel in sorted({r["novel"] for r in rows}):
            ids = [i for i, r in enumerate(rows) if r["novel"] == novel]
            for method in methods:
                by_novel[novel][method] = {"correct": sum(correctness[method][i] for i in ids), "total": len(ids)}
        oracle_methods = ["old_tail", "c4fix", "c1perm", "c3stage1"]
        oracle = sum(any(correctness[m][i] for m in oracle_methods) for i in range(len(rows)))
        report["masks"][mask] = {
            "summary": summary,
            "comparisons": comparisons,
            "by_type": by_type,
            "by_novel": by_novel,
            "oracle": {"methods": oracle_methods, "correct": oracle, "total": len(rows)},
        }
    return report


def write_artifacts(rows: list[dict[str, Any]], report: dict[str, Any], graph_root: Path, next_root: Path, prior_root: Path) -> None:
    methods = ["old_tail", "old_c3", "prior_c6", *NEXT_METHODS]
    all_letters = {(mask, m): letters_for(rows, m, mask, graph_root, next_root, prior_root) for mask in ("masked", "unmasked") for m in methods}
    fields = ["novel", "qi", "qid", "question_type", "question", "gold_letter"] + [f"{m}_{mask}" for mask in ("masked", "unmasked") for m in methods]
    with (next_root / "per_question_matrix.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for i, row in enumerate(rows):
            out = {key: row.get(key) for key in ("novel", "qi", "qid", "question_type", "question")}
            out["gold_letter"] = LETTERS[row["gold_index"]]
            for mask in ("masked", "unmasked"):
                for method in methods:
                    out[f"{method}_{mask}"] = all_letters[(mask, method)][i] or ""
            writer.writerow(out)
    suspect = []
    for mask in ("masked", "unmasked"):
        for i, row in enumerate(rows):
            audit_methods = ["c4fix", "c1perm", "c3stage1"]
            preds = [all_letters[(mask, m)][i] for m in audit_methods]
            grounded = [load_json(new_path(next_root, m, mask, row["novel"], row["qi"])).get("evidence_ids", []) for m in audit_methods]
            if len(set(preds)) == 1 and preds[0] and not correct(preds[0], row) and sum(bool(x) for x in grounded) >= 2:
                suspect.append({"mask": mask, "novel": row["novel"], "qi": row["qi"], "question": row["question"], "gold_letter": LETTERS[row["gold_index"]], "unanimous_letter": preds[0], "grounded_methods": sum(bool(x) for x in grounded)})
    (next_root / "suspect_gold_cases.json").write_text(json.dumps(suspect, ensure_ascii=False, indent=1), encoding="utf-8")


def derive_c3_stage1(rows: list[dict[str, Any]], next_root: Path) -> None:
    """Materialize the empirically safer first stage without another model call."""
    for mask in ("masked", "unmasked"):
        for row in rows:
            source = load_json(new_path(next_root, "c3disc", mask, row["novel"], row["qi"]))
            trace = source.get("trace", [])
            if not trace:
                raise FileNotFoundError(f"missing c3 trace for {mask}/{row['novel']}/q{row['qi']}")
            result = dict(trace[0].get("normalized", {}))
            result.update({key: source.get(key) for key in ("novel", "qi", "qid", "question", "choices", "gold_index", "gold_text", "mask", "answer_model", "question_type", "masked_at", "mask_policy")})
            result.update({"method": "c3_first_stage", "parent_method": "c3disc", "parent_prompt_hash": source.get("prompt_hash"), "prompt_version": "c3-stage1-derived-v1", "prompt_hash": "derived-from-c3disc-stage1", "trace": [trace[0]], "stopped_after_stage": 1})
            path = new_path(next_root, "c3stage1", mask, row["novel"], row["qi"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


def markdown(report: dict[str, Any]) -> str:
    lines = ["# 方案 C 下一轮实验报告", ""]
    for mask, block in report["masks"].items():
        lines += [f"## {mask}", "", "| 方法 | 正确率 | 对照 | 提升 | Holm p |", "|---|---:|---|---:|---:|"]
        comps = {x["method"]: x for x in block["comparisons"]}
        for method, vals in sorted(block["summary"].items(), key=lambda x: -x[1]["accuracy"]):
            comp = comps.get(method)
            lines.append(f"| {method} | {vals['correct']}/{vals['total']} ({vals['accuracy']:.1%}) | {comp['baseline'] if comp else '—'} | {comp['delta']:+.1%} | {comp['mcnemar']['p_holm']:.4f} |" if comp else f"| {method} | {vals['correct']}/{vals['total']} ({vals['accuracy']:.1%}) | — | — | — |")
        lines += ["", f"候选 oracle：{block['oracle']['correct']}/{block['oracle']['total']}。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c")
    parser.add_argument("--next-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_next_round")
    parser.add_argument("--prior-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_improvements")
    args = parser.parse_args()
    rows = all_questions()
    derive_c3_stage1(rows, args.next_root)
    report = evaluate(rows, args.graph_root, args.next_root, args.prior_root)
    write_artifacts(rows, report, args.graph_root, args.next_root, args.prior_root)
    (args.next_root / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    text = markdown(report)
    (args.next_root / "REPORT.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
