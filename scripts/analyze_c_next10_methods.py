"""External-validation analysis for the selected methods on the second ten novels."""

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

from analyze_c_improvements import bootstrap_delta, correct, holm, mcnemar_exact
from build_c_next10_graphs import NOVELS, merged_cases
from c_option_methods import LETTERS, normalize_letter, question_type

METHODS = ["tail", "c1", "c2", "c4", "c3first", "c6"]


def rows_for(novels: list[str]) -> list[dict[str, Any]]:
    cases = merged_cases(novels)
    return [{"novel": novel, "qi": qi, **q, "question_type": question_type(q["question"])} for novel in novels for qi, q in enumerate(cases[novel]["questions"])]


def answer_path(root: Path, method: str, mask: str, row: dict[str, Any]) -> Path:
    return root / method / mask / row["novel"] / f"q{row['qi']:02d}.json"


def load_answer(root: Path, method: str, mask: str, row: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    path = answer_path(root, method, mask, row)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return normalize_letter(data.get("selected_letter")), data


def evaluate(rows: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {"masks": {}, "diagnostics": {}}
    for mask in ("masked", "unmasked"):
        letters = {method: [load_answer(root, method, mask, row)[0] for row in rows] for method in METHODS}
        correctness = {method: [correct(letter, row) for letter, row in zip(values, rows)] for method, values in letters.items()}
        summary = {method: {"correct": sum(correctness[method]), "total": len(rows), "accuracy": sum(correctness[method]) / len(rows), "parsed": sum(x is not None for x in letters[method])} for method in METHODS}
        comparisons = []
        for method in METHODS[1:]:
            stat = mcnemar_exact(correctness[method], correctness["tail"])
            delta = summary[method]["accuracy"] - summary["tail"]["accuracy"]
            comparisons.append({"method": method, "baseline": "tail", "delta": delta, "bootstrap_95": bootstrap_delta(rows, correctness[method], correctness["tail"]), "practical_gain": delta >= 0.05, "mcnemar": stat})
        holm(comparisons)
        for item in comparisons:
            item["significant_and_practical"] = item["practical_gain"] and item["mcnemar"]["p_holm"] < 0.05
        by_type = defaultdict(dict)
        by_novel = defaultdict(dict)
        for qtype in sorted({r["question_type"] for r in rows}):
            ids = [i for i, r in enumerate(rows) if r["question_type"] == qtype]
            for method in METHODS:
                by_type[qtype][method] = {"correct": sum(correctness[method][i] for i in ids), "total": len(ids)}
        for novel in NOVELS:
            ids = [i for i, r in enumerate(rows) if r["novel"] == novel]
            for method in METHODS:
                by_novel[novel][method] = {"correct": sum(correctness[method][i] for i in ids), "total": len(ids)}
        oracle = sum(any(correctness[m][i] for m in METHODS) for i in range(len(rows)))
        report["masks"][mask] = {"summary": summary, "comparisons": comparisons, "by_type": by_type, "by_novel": by_novel, "oracle": {"correct": oracle, "total": len(rows)}}
        for method in METHODS:
            answer_rows = [load_answer(root, method, mask, row)[1] for row in rows]
            elapsed = [r.get("elapsed_seconds") for r in answer_rows if isinstance(r.get("elapsed_seconds"), (int, float))]
            report["diagnostics"][f"{method}/{mask}"] = {"answers": len(answer_rows), "unparsed": sum(normalize_letter(r.get("selected_letter")) is None for r in answer_rows), "average_elapsed_seconds": statistics.mean(elapsed) if elapsed else None, "prediction_distribution": dict(Counter(normalize_letter(r.get("selected_letter")) or "unparsed" for r in answer_rows)), "arbitrated": sum(bool(r.get("arbitrated")) for r in answer_rows)}
    return report


def write_matrix(rows: list[dict[str, Any]], root: Path) -> None:
    fields = ["novel", "qi", "qid", "question_type", "question", "gold_letter"] + [f"{method}_{mask}" for mask in ("masked", "unmasked") for method in METHODS]
    with (root / "per_question_matrix.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = {key: row.get(key) for key in ("novel", "qi", "qid", "question_type", "question")}
            out["gold_letter"] = LETTERS[row["gold_index"]]
            for mask in ("masked", "unmasked"):
                for method in METHODS:
                    out[f"{method}_{mask}"] = load_answer(root, method, mask, row)[0] or ""
            writer.writerow(out)


def markdown(report: dict[str, Any]) -> str:
    lines = ["# 方案 C：第二组 10 本小说外部验证", ""]
    for mask, block in report["masks"].items():
        lines += [f"## {mask}", "", "| 方法 | 正确率 | 相对尾窗口 | Holm p | 达标 |", "|---|---:|---:|---:|---|"]
        comparisons = {x["method"]: x for x in block["comparisons"]}
        for method, values in sorted(block["summary"].items(), key=lambda x: -x[1]["accuracy"]):
            comp = comparisons.get(method)
            lines.append(f"| {method} | {values['correct']}/{values['total']} ({values['accuracy']:.1%}) | {comp['delta']:+.1%} | {comp['mcnemar']['p_holm']:.4f} | {'是' if comp['significant_and_practical'] else '否'} |" if comp else f"| {method} | {values['correct']}/{values['total']} ({values['accuracy']:.1%}) | — | — | 基线 |")
        lines += ["", f"候选 oracle：{block['oracle']['correct']}/{block['oracle']['total']}。", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_next10_methods")
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    args = parser.parse_args()
    rows = rows_for(args.novels)
    report = evaluate(rows, args.out_root)
    write_matrix(rows, args.out_root)
    (args.out_root / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    text = markdown(report)
    (args.out_root / "REPORT.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
