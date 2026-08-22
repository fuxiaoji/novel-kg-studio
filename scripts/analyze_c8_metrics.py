"""Evaluate C8 with prior-stripped and matched-budget graph-specific metrics."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_c_combined20 import load_rows  # noqa: E402
from c8_graph_passage import LETTERS  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, SECOND10, answer_path  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"


def load_json_answers(root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in root.glob("*/*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        result[row["qid"]] = row
    return result


def mcnemar(new: list[bool], old: list[bool]) -> dict[str, Any]:
    wins = sum(a and not b for a, b in zip(new, old))
    losses = sum(b and not a for a, b in zip(new, old))
    p = float(binomtest(min(wins, losses), wins + losses, 0.5).pvalue) if wins + losses else 1.0
    return {"wins": wins, "losses": losses, "p": p}


def cluster_bootstrap(rows: list[dict[str, Any]], new_key: str, old_key: str, iterations: int = 10000) -> list[float]:
    rng = random.Random(20260811)
    novels = sorted({row["novel"] for row in rows}, key=int)
    by_novel = {novel: [row for row in rows if row["novel"] == novel] for novel in novels}
    values = []
    for _ in range(iterations):
        sample = []
        for _ in novels:
            sample.extend(by_novel[rng.choice(novels)])
        values.append(sum(row[new_key] - row[old_key] for row in sample) / len(sample))
    values.sort()
    return [values[int(0.025 * iterations)], values[int(0.975 * iterations) - 1]]


def summarize(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    correct = sum(row[key] for row in rows)
    return {"correct": correct, "total": len(rows), "accuracy": correct / len(rows) if rows else 0.0}


def macro_by_novel(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    by_novel: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_novel[row["novel"]].append(row)
    accuracies = [sum(row[key] for row in group) / len(group) for group in by_novel.values()]
    return {
        "novels": len(accuracies),
        "macro_accuracy": sum(accuracies) / len(accuracies) if accuracies else 0.0,
    }


def paired_gain(rows: list[dict[str, Any]], new_key: str, old_key: str) -> dict[str, Any]:
    stat = mcnemar([row[new_key] for row in rows], [row[old_key] for row in rows])
    stat["delta_accuracy"] = (
        sum(row[new_key] - row[old_key] for row in rows) / len(rows) if rows else 0.0
    )
    if len({row["novel"] for row in rows}) >= 5:
        stat["novel_cluster_bootstrap95"] = cluster_bootstrap(rows, new_key, old_key)
    return stat


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=BASE / "dqa_qwen_c8_20")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    old_rows = load_rows()
    by_key = {(row["novel"], int(row["qi"])): row for row in old_rows}
    qwen_closed = load_json_answers(BASE / "dqa_qwen_question_only20" / "answers")
    deepseek_closed = load_json_answers(BASE / "dqa_deepseek_v4flash_full_vs_question20_nothinking" / "answers" / "question_only")
    compression = load_json_answers(BASE / "dqa_qwen_compress20" / "answers")
    goldonly = load_json_answers(BASE / "dqa_qwen_goldonly20" / "answers")
    c12 = load_json_answers(BASE / "dqa_qwen_c12_consensus20" / "answers")
    rows = []
    for novel in NOVELS:
        for method in ("bm25", "graph"):
            count = len(list((args.root / method / "unmasked" / novel).glob("q*.json"))) if (args.root / method / "unmasked" / novel).exists() else 0
            if not args.allow_partial and count != sum(1 for key in by_key if key[0] == novel):
                raise RuntimeError(f"incomplete {method}/{novel}: {count}")
        q_indices = sorted(qi for n, qi in by_key if n == novel)
        for qi in q_indices:
            paths = {method: answer_path(args.root, method, novel, qi) for method in ("bm25", "graph")}
            if not all(path.exists() for path in paths.values()):
                if args.allow_partial:
                    continue
                raise FileNotFoundError(paths)
            old = by_key[(novel, qi)]
            answers = {method: json.loads(path.read_text(encoding="utf-8")) for method, path in paths.items()}
            qid = old["qid"]
            gold = old["gold_letter"]
            row = {
                "novel": novel,
                "qi": qi,
                "qid": qid,
                "batch": old["batch"],
                "question_type": old["question_type"],
                "gold_letter": gold,
                "qwen_closed": bool(qwen_closed[qid]["correct"]),
                "deepseek_closed": bool(deepseek_closed[qid]["correct"]),
                "tail": old["tail_unmasked"] == gold,
                "c4": old["c4_unmasked"] == gold,
                "compress": bool(compression.get(qid, {}).get("correct")),
                "goldonly": bool(goldonly.get(qid, {}).get("correct")),
                "c12_consensus": bool(c12.get(qid, {}).get("correct")),
            }
            for method, answer in answers.items():
                row[method] = answer.get("selected_letter") == gold
                row[f"{method}_letter"] = answer.get("selected_letter")
            bm25_chunks = [chunk.get("id") for chunk in answers["bm25"].get("retrieval", {}).get("chunks", [])]
            graph_chunks = [chunk.get("id") for chunk in answers["graph"].get("retrieval", {}).get("chunks", [])]
            row["same_passage_set"] = bm25_chunks == graph_chunks
            row["graph_links"] = len(answers["graph"].get("retrieval", {}).get("links", []))
            row["graph_bm25_agree"] = row["graph_letter"] == row["bm25_letter"]
            # Frozen after inspecting only first-batch BM25-vs-tail development
            # errors: retrieval had a +5 net advantage on explicit killer or
            # mastermind questions and no advantage on the other broad types.
            row["killer_gate"] = row["graph"] if row["question_type"] == "killer" else row["tail"]
            rows.append(row)

    qwen_hard = [row for row in rows if not row["qwen_closed"]]
    conservative_hard = [row for row in rows if not row["qwen_closed"] and not row["deepseek_closed"]]
    report: dict[str, Any] = {
        "definitions": {
            "qwen_hard": "Qwen2.5-7B question+options baseline incorrect",
            "conservative_hard": "both Qwen2.5-7B and DeepSeek V4 Flash question+options baselines incorrect",
            "evidence_rescue_rate": "correct among Qwen closed-book incorrect questions",
            "prior_preservation_rate": "remains correct among Qwen closed-book correct questions",
            "net_novel_gain": "(closed-book wrong rescued - closed-book correct harmed) / all questions",
            "graph_specific_gain": "C8 graph minus matched-prompt, matched-18-passage BM25",
            "macro_accuracy": "mean per-novel accuracy, giving every novel equal weight",
            "closed_book_vote": "number of the two closed-book models that answer correctly (0, 1, or 2)",
            "killer_gate": "frozen development rule: graph for killer/mastermind questions, otherwise tail",
            "contamination_caveat": (
                "Closed-book filtering reduces sensitivity to guessing, world knowledge, and possible training-memory "
                "effects, but it cannot prove that a novel was absent from model pretraining."
            ),
        },
        "counts": {"all": len(rows), "qwen_hard": len(qwen_hard), "conservative_hard": len(conservative_hard)},
        "sets": {},
        "by_batch": {},
        "by_type": {},
        "by_closed_book_vote": {},
        "diagnostics": {},
    }
    for set_name, subset in (("all", rows), ("qwen_hard", qwen_hard), ("conservative_hard", conservative_hard)):
        block = {method: summarize(subset, method) for method in ("qwen_closed", "tail", "compress", "goldonly", "c4", "bm25", "graph", "killer_gate", "c12_consensus")}
        for method in block:
            block[method].update(macro_by_novel(subset, method))
        for method in ("compress", "goldonly", "bm25", "graph", "c12_consensus"):
            block[method]["vs_tail"] = paired_gain(subset, method, "tail")
            block[method]["vs_c4"] = paired_gain(subset, method, "c4")
        block["graph"]["vs_bm25"] = paired_gain(subset, "graph", "bm25")
        block["killer_gate"]["vs_tail"] = paired_gain(subset, "killer_gate", "tail")
        report["sets"][set_name] = block
    closed_correct = [row for row in rows if row["qwen_closed"]]
    for method in ("tail", "compress", "goldonly", "c4", "bm25", "graph", "killer_gate", "c12_consensus"):
        rescue = sum(row[method] for row in qwen_hard)
        preserved = sum(row[method] for row in closed_correct)
        report["sets"]["all"][method].update(
            {
                "rescued_closed_book_errors": rescue,
                "harmed_closed_book_correct": len(closed_correct) - preserved,
                "evidence_rescue_rate": rescue / len(qwen_hard) if qwen_hard else 0.0,
                "prior_preservation_rate": preserved / len(closed_correct) if closed_correct else 0.0,
                "net_novel_gain": (rescue - (len(closed_correct) - preserved)) / len(rows) if rows else 0.0,
            }
        )
    for name, novels in (("first10", FIRST10), ("second10", SECOND10)):
        subset = [row for row in rows if row["novel"] in novels]
        report["by_batch"][name] = {method: summarize(subset, method) for method in ("tail", "compress", "goldonly", "c4", "bm25", "graph", "killer_gate", "c12_consensus")}
    for qtype in sorted({row["question_type"] for row in rows}):
        subset = [row for row in rows if row["question_type"] == qtype]
        report["by_type"][qtype] = {method: summarize(subset, method) for method in ("tail", "compress", "goldonly", "c4", "bm25", "graph", "killer_gate", "c12_consensus")}
    for vote in range(3):
        subset = [row for row in rows if int(row["qwen_closed"]) + int(row["deepseek_closed"]) == vote]
        report["by_closed_book_vote"][str(vote)] = {
            "interpretation": (
                "both closed-book models wrong" if vote == 0 else
                "exactly one closed-book model correct" if vote == 1 else
                "both closed-book models correct"
            ),
            **{method: summarize(subset, method) for method in ("tail", "compress", "goldonly", "c4", "bm25", "graph", "killer_gate", "c12_consensus")},
        }
    report["diagnostics"] = {
        "same_passage_set": sum(row["same_passage_set"] for row in rows),
        "questions": len(rows),
        "graph_bm25_answer_agreement": sum(row["graph_bm25_agree"] for row in rows) / len(rows) if rows else 0.0,
        "average_verified_links": sum(row["graph_links"] for row in rows) / len(rows) if rows else 0.0,
    }
    args.root.mkdir(parents=True, exist_ok=True)
    (args.root / "analysis_metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    fields = list(rows[0]) if rows else []
    with (args.root / "per_question_metrics.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"counts": report["counts"], "sets": report["sets"], "by_batch": report["by_batch"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
