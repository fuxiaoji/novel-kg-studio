"""C0/C5/C7 evaluation and paired significance analysis for option-C improvements."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from c_option_methods import LETTERS, normalize_letter, question_type
from run_c_improvements import NOVELS, merged_cases, output_path

ANSWER_MODEL = "qwen2.5:7b-32k"
MTAG = f"|{ANSWER_MODEL}"
OLD_GRAPH = {
    "v4": "r4",
    "v5a": "r5a",
    "v5b": "r5b",
    "v7": "r7",
}


def _old_key(nid: str, qi: int, salt: str) -> str:
    return hashlib.sha1(f"{nid}|{qi}|v1|{salt}{MTAG}".encode("utf-8")).hexdigest()[:10]


def _base_key(nid: str, qi: int) -> str:
    return hashlib.sha1(f"{nid}|{qi}|v1{MTAG}".encode("utf-8")).hexdigest()[:10]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def old_answer(graph_root: Path, nid: str, qi: int, method: str, mask: str) -> tuple[str | None, str]:
    novel_dir = graph_root / "novels" / nid
    if method in OLD_GRAPH:
        prefix = "graph_" if mask == "masked" else "graphnm_"
        path = novel_dir / f"{prefix}{_old_key(nid, qi, OLD_GRAPH[method])}.json"
    elif method == "tail":
        path = novel_dir / f"tail_{_base_key(nid, qi)}.json"
    elif method == "compress":
        path = novel_dir / f"comp_{_base_key(nid, qi)}.json"
    else:
        raise KeyError(method)
    row = _load_json(path) or {}
    answer = str(row.get("answer", ""))
    return normalize_letter(answer), answer


def new_answer(out_root: Path, nid: str, qi: int, method: str, mask: str) -> tuple[str | None, dict[str, Any] | None]:
    row = _load_json(output_path(out_root, method, mask, nid, qi))
    return (normalize_letter(row.get("selected_letter")) if row else None), row


def all_questions() -> list[dict[str, Any]]:
    cases = merged_cases()
    rows = []
    for nid in NOVELS:
        for qi, q in enumerate(cases[nid]["questions"]):
            rows.append({"novel": nid, "qi": qi, **q, "question_type": question_type(q["question"])})
    return rows


def correct(letter: str | None, row: dict[str, Any]) -> bool:
    gi = row.get("gold_index")
    return bool(letter and isinstance(gi, int) and 0 <= gi < 4 and letter == LETTERS[gi])


def c0_baseline(rows: list[dict[str, Any]], graph_root: Path, out_root: Path) -> dict[str, Any]:
    matrix_path = graph_root / "error_matrix_10.json"
    matrix = {(r["novel"], int(r["qi"])): r for r in json.loads(matrix_path.read_text(encoding="utf-8"))}
    conflicts = []
    summaries = {}
    for mask in ("masked", "unmasked"):
        for method in [*OLD_GRAPH, "tail", "compress"]:
            vals = []
            parsed = 0
            for row in rows:
                letter, answer = old_answer(graph_root, row["novel"], row["qi"], method, mask)
                parsed += int(letter is not None)
                vals.append(correct(letter, row))
                matrix_col = None
                if method in OLD_GRAPH:
                    matrix_col = f"graph_{method}_{'m' if mask == 'masked' else 'u'}"
                elif method in {"tail", "compress"}:
                    matrix_col = method
                judged = matrix[(row["novel"], row["qi"])].get(matrix_col) if matrix_col else None
                if isinstance(judged, bool) and judged != vals[-1]:
                    conflicts.append(
                        {
                            "novel": row["novel"],
                            "qi": row["qi"],
                            "method": method,
                            "mask": mask,
                            "gold_letter": LETTERS[row["gold_index"]],
                            "parsed_letter": letter,
                            "deepseek_correct": judged,
                            "letter_correct": vals[-1],
                            "answer": answer,
                        }
                    )
            summaries[f"old_{method}_{mask}"] = {"correct": sum(vals), "total": len(vals), "accuracy": sum(vals) / len(vals), "parsed": parsed}
    flags = []
    suspicious = re_compile_suspicious()
    for row in rows:
        reasons = []
        text = f"{row['question']} {row.get('gold_text', '')}"
        if suspicious.search(text):
            reasons.append("translation_or_format")
        if len(str(row.get("gold_text", ""))) < 2:
            reasons.append("very_short_gold")
        if reasons:
            flags.append({"novel": row["novel"], "qi": row["qi"], "question": row["question"], "gold": row.get("gold_text"), "flags": reasons})
    payload = {"summaries": summaries, "judge_conflicts": conflicts, "quality_flags": flags}
    (out_root / "c0_corrected_baseline.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def re_compile_suspicious():
    import re

    return re.compile(r"<br>|\bpieces? of\b|\b200 acres\b|\bMists? Drawn\b|\bKagoshima\b|\bXi Yan\b", re.I)


def method_letters(rows: list[dict[str, Any]], graph_root: Path, out_root: Path, method: str, mask: str) -> list[str | None]:
    result = []
    for row in rows:
        if method.startswith("old_"):
            letter, _ = old_answer(graph_root, row["novel"], row["qi"], method[4:], mask)
        else:
            letter, _ = new_answer(out_root, row["novel"], row["qi"], method, mask)
        result.append(letter)
    return result


def write_predictions(out_root: Path, method: str, mask: str, rows: list[dict[str, Any]], letters: list[str | None], meta: list[dict]) -> None:
    for row, letter, extra in zip(rows, letters, meta):
        path = output_path(out_root, method, mask, row["novel"], row["qi"])
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "method": method,
            "novel": row["novel"],
            "qi": row["qi"],
            "qid": row["qid"],
            "question": row["question"],
            "choices": row["choices"],
            "gold_index": row["gold_index"],
            "gold_text": row.get("gold_text"),
            "mask": mask,
            "selected_letter": letter,
            "selected_text": row["choices"][LETTERS.index(letter)] if letter in LETTERS else "",
            **extra,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def build_c5_router(rows: list[dict[str, Any]], graph_root: Path, out_root: Path, mask: str) -> tuple[list[str | None], list[dict]]:
    candidates = ["c1", "c2", "c3", "c4", "old_tail", "old_compress"]
    letters_by = {m: method_letters(rows, graph_root, out_root, m, mask) for m in candidates}
    chosen_letters, metadata = [], []
    for i, row in enumerate(rows):
        train = [j for j, r in enumerate(rows) if r["novel"] != row["novel"]]
        same_type = [j for j in train if rows[j]["question_type"] == row["question_type"]]
        pool = same_type if len(same_type) >= 5 else train
        scores = {
            m: sum(correct(letters_by[m][j], rows[j]) for j in pool) / len(pool)
            for m in candidates
        }
        chosen = max(candidates, key=lambda m: (scores[m], -candidates.index(m)))
        chosen_letters.append(letters_by[chosen][i])
        metadata.append({"router_type": row["question_type"], "routed_method": chosen, "training_scores": scores})
    return chosen_letters, metadata


def build_c7_combination(rows: list[dict[str, Any]], graph_root: Path, out_root: Path, mask: str) -> tuple[list[str | None], list[dict]]:
    candidates = [m for m in ("c1", "c2", "c3", "c4", "c5", "c6") if output_path(out_root, m, mask, rows[0]["novel"], rows[0]["qi"]).exists()]
    baseline_candidates = [f"old_{m}" for m in [*OLD_GRAPH, "tail", "compress"]]
    letters_by = {m: method_letters(rows, graph_root, out_root, m, mask) for m in candidates + baseline_candidates}
    chosen_letters, metadata = [], []
    for i, row in enumerate(rows):
        train = [j for j, r in enumerate(rows) if r["novel"] != row["novel"]]
        base_scores = {m: sum(correct(letters_by[m][j], rows[j]) for j in train) / len(train) for m in baseline_candidates}
        best_base = max(base_scores, key=base_scores.get)
        new_scores = {m: sum(correct(letters_by[m][j], rows[j]) for j in train) / len(train) for m in candidates}
        best_new = max(new_scores, key=new_scores.get)
        # Only combine an independently improved component when it clears the predeclared practical threshold on training novels.
        chosen = best_new if new_scores[best_new] - base_scores[best_base] >= 0.05 else best_base
        chosen_letters.append(letters_by[chosen][i])
        metadata.append({"selected_component": chosen, "training_new_scores": new_scores, "training_baseline_scores": base_scores})
    return chosen_letters, metadata


def mcnemar_exact(new: list[bool], base: list[bool]) -> dict[str, Any]:
    win = sum(n and not b for n, b in zip(new, base))
    loss = sum(b and not n for n, b in zip(new, base))
    p = float(binomtest(min(win, loss), win + loss, 0.5).pvalue) if win + loss else 1.0
    return {"wins": win, "losses": loss, "discordant": win + loss, "p_raw": p}


def bootstrap_delta(rows: list[dict[str, Any]], new: list[bool], base: list[bool], iterations: int = 10000) -> tuple[float, float]:
    rng = random.Random(20260808)
    novel_ids = list(dict.fromkeys(str(r["novel"]) for r in rows))
    by_novel = {nid: [i for i, r in enumerate(rows) if str(r["novel"]) == nid] for nid in novel_ids}
    if not novel_ids or any(not ids for ids in by_novel.values()):
        raise ValueError("bootstrap_delta requires at least one row for every sampled novel")
    deltas = []
    for _ in range(iterations):
        sample = []
        for _ in novel_ids:
            nid = rng.choice(novel_ids)
            ids = by_novel[nid]
            sample.extend(rng.choice(ids) for _ in ids)
        deltas.append(sum(int(new[i]) - int(base[i]) for i in sample) / len(sample))
    deltas.sort()
    return deltas[int(iterations * 0.025)], deltas[min(int(iterations * 0.975), iterations - 1)]


def holm(results: list[dict[str, Any]]) -> None:
    ordered = sorted(enumerate(results), key=lambda x: x[1]["mcnemar"]["p_raw"])
    running = 0.0
    n = len(ordered)
    for rank, (idx, row) in enumerate(ordered):
        adjusted = min(1.0, row["mcnemar"]["p_raw"] * (n - rank))
        running = max(running, adjusted)
        results[idx]["mcnemar"]["p_holm"] = running


def evaluate(rows: list[dict[str, Any]], graph_root: Path, out_root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {"masks": {}}
    for mask in ("masked", "unmasked"):
        c5_letters, c5_meta = build_c5_router(rows, graph_root, out_root, mask)
        write_predictions(out_root, "c5", mask, rows, c5_letters, c5_meta)
        c7_letters, c7_meta = build_c7_combination(rows, graph_root, out_root, mask)
        write_predictions(out_root, "c7", mask, rows, c7_letters, c7_meta)
        all_methods = [f"old_{m}" for m in [*OLD_GRAPH, "tail", "compress"]] + [m for m in ("c1", "c2", "c3", "c4", "c5", "c6", "c7") if output_path(out_root, m, mask, rows[0]["novel"], rows[0]["qi"]).exists()]
        letters_by = {m: method_letters(rows, graph_root, out_root, m, mask) for m in all_methods}
        correctness = {m: [correct(x, row) for x, row in zip(letters_by[m], rows)] for m in all_methods}
        old_methods = [m for m in all_methods if m.startswith("old_")]
        baseline = max(old_methods, key=lambda m: sum(correctness[m]))
        summary = {
            m: {
                "correct": sum(correctness[m]),
                "total": len(rows),
                "accuracy": sum(correctness[m]) / len(rows),
                "parsed": sum(x is not None for x in letters_by[m]),
            }
            for m in all_methods
        }
        comparisons = []
        for method in [m for m in all_methods if not m.startswith("old_")]:
            stat = mcnemar_exact(correctness[method], correctness[baseline])
            ci = bootstrap_delta(rows, correctness[method], correctness[baseline])
            delta = summary[method]["accuracy"] - summary[baseline]["accuracy"]
            comparisons.append({"method": method, "baseline": baseline, "delta": delta, "bootstrap_95": ci, "practical_gain": delta >= 0.05, "mcnemar": stat})
        holm(comparisons)
        for row in comparisons:
            row["significant_and_practical"] = row["practical_gain"] and row["mcnemar"]["p_holm"] < 0.05
        by_type = defaultdict(dict)
        for qtype in sorted({r["question_type"] for r in rows}):
            ids = [i for i, r in enumerate(rows) if r["question_type"] == qtype]
            for method in all_methods:
                by_type[qtype][method] = {"correct": sum(correctness[method][i] for i in ids), "total": len(ids)}
        by_novel = defaultdict(dict)
        for nid in NOVELS:
            ids = [i for i, r in enumerate(rows) if r["novel"] == nid]
            for method in all_methods:
                by_novel[nid][method] = {"correct": sum(correctness[method][i] for i in ids), "total": len(ids)}
        report["masks"][mask] = {"baseline": baseline, "summary": summary, "comparisons": comparisons, "by_type": by_type, "by_novel": by_novel}
    return report


def markdown_report(report: dict[str, Any], c0: dict[str, Any]) -> str:
    lines = ["# 方案 C 独立改进实验报告", "", f"C0 判分冲突：{len(c0['judge_conflicts'])} 条；数据质量标记：{len(c0['quality_flags'])} 题。", ""]
    for mask, block in report["masks"].items():
        lines += [f"## {mask}", "", f"修正后的最佳旧基线：`{block['baseline']}`", "", "| 方法 | 正确 | 正确率 | 相对提升 | Holm p | 达标 |", "|---|---:|---:|---:|---:|---|"]
        comparisons = {r["method"]: r for r in block["comparisons"]}
        for method, vals in sorted(block["summary"].items(), key=lambda x: -x[1]["accuracy"]):
            comp = comparisons.get(method)
            lines.append(
                f"| {method} | {vals['correct']}/{vals['total']} | {vals['accuracy']:.1%} | "
                + (f"{comp['delta']:+.1%} | {comp['mcnemar']['p_holm']:.4f} | {'是' if comp['significant_and_practical'] else '否'} |" if comp else "— | — | 基线 |")
            )
        lines.append("")
    return "\n".join(lines)


def write_tables(rows: list[dict[str, Any]], report: dict[str, Any], c0: dict[str, Any], graph_root: Path, out_root: Path) -> None:
    """Write the machine-readable tables promised by the experiment manifest."""
    methods = list(report["masks"]["masked"]["summary"])
    letters = {
        (mask, method): method_letters(rows, graph_root, out_root, method, mask)
        for mask in ("masked", "unmasked")
        for method in methods
    }

    with (out_root / "per_question_matrix.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        fields = ["novel", "qi", "qid", "question_type", "question", "gold_letter"] + [
            f"{method}_{mask}" for mask in ("masked", "unmasked") for method in methods
        ]
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for i, row in enumerate(rows):
            item = {k: row.get(k) for k in ("novel", "qi", "qid", "question_type", "question")}
            item["gold_letter"] = LETTERS[row["gold_index"]] if row.get("gold_index") in range(4) else ""
            for mask in ("masked", "unmasked"):
                for method in methods:
                    item[f"{method}_{mask}"] = letters[(mask, method)][i] or ""
            writer.writerow(item)

    with (out_root / "summary_table.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["mask", "method", "correct", "total", "accuracy", "parsed", "baseline"])
        writer.writeheader()
        for mask, block in report["masks"].items():
            for method, vals in block["summary"].items():
                writer.writerow({"mask": mask, "method": method, **vals, "baseline": method == block["baseline"]})

    for dimension, filename in (("by_type", "question_type_table.csv"), ("by_novel", "novel_table.csv")):
        key_name = "question_type" if dimension == "by_type" else "novel"
        with (out_root / filename).open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["mask", key_name, "method", "correct", "total", "accuracy"])
            writer.writeheader()
            for mask, block in report["masks"].items():
                for key, method_rows in block[dimension].items():
                    for method, vals in method_rows.items():
                        writer.writerow({"mask": mask, key_name: key, "method": method, **vals, "accuracy": vals["correct"] / vals["total"]})

    conflicts = c0.get("judge_conflicts", [])
    conflict_fields = sorted({key for row in conflicts for key in row})
    with (out_root / "judge_conflicts.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=conflict_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(conflicts)

    failures: list[dict[str, Any]] = []
    runtime: dict[str, Any] = {}
    version_audit: dict[str, Any] = {}
    for mask in ("masked", "unmasked"):
        baseline = report["masks"][mask]["baseline"]
        base_letters = letters[(mask, baseline)]
        c6_letters = letters[(mask, "c6")]
        for i, row in enumerate(rows):
            base_ok = correct(base_letters[i], row)
            c6_ok = correct(c6_letters[i], row)
            if base_ok != c6_ok:
                failures.append({
                    "mask": mask, "novel": row["novel"], "qi": row["qi"], "qid": row["qid"],
                    "question_type": row["question_type"], "question": row["question"],
                    "gold_letter": LETTERS[row["gold_index"]], "baseline": baseline,
                    "baseline_letter": base_letters[i], "c6_letter": c6_letters[i],
                    "outcome": "c6_gain" if c6_ok else "c6_regression",
                })
        for method in ("c1", "c2", "c3", "c4", "c6"):
            answer_rows = []
            for nid in NOVELS:
                answer_rows.extend(json.loads(p.read_text(encoding="utf-8")) for p in sorted((out_root / method / mask / nid).glob("q*.json")))
            elapsed = [r.get("elapsed_seconds") for r in answer_rows if isinstance(r.get("elapsed_seconds"), (int, float))]
            runtime[f"{method}/{mask}"] = {
                "answers": len(answer_rows),
                "unparsed": sum(normalize_letter(r.get("selected_letter")) is None for r in answer_rows),
                "average_elapsed_seconds": sum(elapsed) / len(elapsed) if elapsed else None,
                "trace_steps": sum(len(r.get("trace", [])) for r in answer_rows),
                "fallback_count": sum(bool(r.get("fallback_used")) for r in answer_rows),
                "arbitrated_count": sum(bool(r.get("arbitrated")) for r in answer_rows),
            }
            hashes = Counter(str(r.get("prompt_hash", "missing")) for r in answer_rows)
            version_audit[f"{method}/{mask}"] = {
                "prompt_hash_counts": dict(hashes),
                "homogeneous": len(hashes) == 1,
            }
    (out_root / "failure_cases.json").write_text(json.dumps(failures, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_root / "runtime_metrics.json").write_text(json.dumps(runtime, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_root / "version_audit.json").write_text(json.dumps(version_audit, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c")
    parser.add_argument("--out-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_improvements")
    args = parser.parse_args()
    args.out_root.mkdir(parents=True, exist_ok=True)
    rows = all_questions()
    c0 = c0_baseline(rows, args.graph_root, args.out_root)
    report = evaluate(rows, args.graph_root, args.out_root)
    write_tables(rows, report, c0, args.graph_root, args.out_root)
    (args.out_root / "analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out_root / "REPORT.md").write_text(markdown_report(report, c0), encoding="utf-8")
    print(markdown_report(report, c0))


if __name__ == "__main__":
    main()
