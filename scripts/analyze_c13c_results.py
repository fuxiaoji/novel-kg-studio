"""Analyze C13d and its ablations against same-model baselines."""

from __future__ import annotations

import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
OUT = BASE / "dqa_deepseek_c13d_overlay20"
FIRST10 = {"26", "27", "28", "30", "31", "33", "40", "53", "56", "79"}


def load_dir(path: Path) -> dict[str, dict]:
    rows = {}
    for file in path.glob("*/*.json"):
        row = json.loads(file.read_text(encoding="utf-8"))
        rows[row["qid"]] = row
    return rows


def score(rows: list[dict], key: str) -> dict:
    correct = sum(bool(row[key]) for row in rows)
    total = len(rows)
    return {"correct": correct, "total": total, "accuracy": correct / total if total else None}


def wilson(correct: int, total: int, z: float = 1.96) -> list[float]:
    p = correct / total
    den = 1 + z * z / total
    center = (p + z * z / (2 * total)) / den
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / den
    return [center - half, center + half]


def exact_mcnemar(a_only: int, b_only: int) -> float:
    n = a_only + b_only
    if not n:
        return 1.0
    low = min(a_only, b_only)
    prob = sum(math.comb(n, k) for k in range(low + 1)) / (2**n)
    return min(1.0, 2 * prob)


def clustered_bootstrap(rows: list[dict], a: str, b: str, iterations: int = 20_000) -> list[float]:
    by_novel: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_novel[row["novel"]].append(row)
    novels = sorted(by_novel, key=int)
    rng = random.Random(20260811)
    diffs = []
    for _ in range(iterations):
        sampled = [rng.choice(novels) for _ in novels]
        a_correct = b_correct = total = 0
        for novel in sampled:
            subset = by_novel[novel]
            total += len(subset)
            a_correct += sum(row[f"{a}_correct"] for row in subset)
            b_correct += sum(row[f"{b}_correct"] for row in subset)
        diffs.append(100 * (a_correct - b_correct) / total)
    diffs.sort()
    return [diffs[int(0.025 * iterations)], diffs[int(0.975 * iterations)]]


def main() -> None:
    overlay = load_dir(OUT / "answers")
    graph_rank = load_dir(BASE / "dqa_deepseek_c13c_20" / "answers")
    no_graph = load_dir(BASE / "dqa_deepseek_c13c_nograph20" / "answers")
    tail = load_dir(BASE / "dqa_deepseek_tail50k_20" / "answers")
    qonly = load_dir(BASE / "dqa_deepseek_v4flash_full_vs_question20_nothinking" / "answers" / "question_only")
    full = load_dir(BASE / "dqa_deepseek_v4flash_full_vs_question20_nothinking" / "answers" / "full_novel")
    assert len(overlay) == len(graph_rank) == len(no_graph) == len(tail) == len(qonly) == len(full) == 164

    rows = []
    for qid, graph in overlay.items():
        chunks = graph["retrieval"]["chunks"]
        passage_text = "\n".join(str(chunk["text"]) for chunk in chunks)
        quote = graph.get("support_quote", "")
        row = {
            "qid": qid,
            "novel": graph["novel"],
            "qi": graph["qi"],
            "question_type": qonly[qid].get("question_type", "unknown"),
            "gold": graph["gold_letter"],
            "overlay_letter": graph["selected_letter"],
            "graph_rank_letter": graph_rank[qid]["selected_letter"],
            "no_graph_letter": no_graph[qid]["selected_letter"],
            "tail_letter": tail[qid]["selected_letter"],
            "question_only_letter": qonly[qid]["selected_letter"],
            "full_novel_letter": full[qid]["selected_letter"],
            "overlay_correct": bool(graph["correct"]),
            "graph_rank_correct": bool(graph_rank[qid]["correct"]),
            "no_graph_correct": bool(no_graph[qid]["correct"]),
            "tail_correct": bool(tail[qid]["correct"]),
            "question_only_correct": bool(qonly[qid]["correct"]),
            "full_novel_correct": bool(full[qid]["correct"]),
            "retrieved_chunks": len(chunks),
            "retrieved_chars": sum(len(str(chunk["text"])) for chunk in chunks),
            "graph_path_count": len(graph["retrieval"].get("paths", [])),
            "grounded_support_quote": bool(quote and quote in passage_text),
        }
        rows.append(row)

    hard = [row for row in rows if not row["question_only_correct"]]
    easy = [row for row in rows if row["question_only_correct"]]
    first = [row for row in rows if row["novel"] in FIRST10]
    second = [row for row in rows if row["novel"] not in FIRST10]
    def paired(subset: list[dict], a: str, b: str) -> dict:
        a_key, b_key = f"{a}_correct", f"{b}_correct"
        a_only = sum(row[a_key] and not row[b_key] for row in subset)
        b_only = sum(row[b_key] and not row[a_key] for row in subset)
        both = sum(row[a_key] and row[b_key] for row in subset)
        neither = len(subset) - a_only - b_only - both
        return {
            "both_correct": both,
            f"{a}_only_correct": a_only,
            f"{b}_only_correct": b_only,
            "both_wrong": neither,
            "absolute_difference_points": 100 * (score(subset, a_key)["accuracy"] - score(subset, b_key)["accuracy"]),
            "exact_mcnemar_p": exact_mcnemar(a_only, b_only),
        }

    by_novel = {}
    for novel in sorted({row["novel"] for row in rows}, key=int):
        subset = [row for row in rows if row["novel"] == novel]
        by_novel[novel] = {method: score(subset, f"{method}_correct") for method in ("overlay", "graph_rank", "no_graph", "tail", "question_only", "full_novel")}
    by_type = {}
    for kind in sorted({row["question_type"] for row in rows}):
        subset = [row for row in rows if row["question_type"] == kind]
        by_type[kind] = {method: score(subset, f"{method}_correct") for method in ("overlay", "graph_rank", "no_graph", "tail", "question_only", "full_novel")}

    summary = {
        "experiment": {
            "method": "C13d option-conditioned retrieval + rebuttal + non-displacing grounded graph overlay",
            "model": "deepseek-v4-flash",
            "thinking": "disabled",
            "mask": "unmasked",
            "questions": len(rows),
        },
        "all": {method: score(rows, f"{method}_correct") for method in ("overlay", "graph_rank", "no_graph", "tail", "question_only", "full_novel")},
        "first10": {method: score(first, f"{method}_correct") for method in ("overlay", "graph_rank", "no_graph", "tail")},
        "second10": {method: score(second, f"{method}_correct") for method in ("overlay", "graph_rank", "no_graph", "tail")},
        "question_only_wrong_subset": {method: score(hard, f"{method}_correct") for method in ("overlay", "graph_rank", "no_graph", "tail", "full_novel")},
        "question_only_correct_subset": {method: score(easy, f"{method}_correct") for method in ("overlay", "graph_rank", "no_graph", "tail", "full_novel")},
        "paired": {
            "overlay_vs_tail_all": paired(rows, "overlay", "tail"),
            "overlay_vs_tail_question_only_wrong": paired(hard, "overlay", "tail"),
            "overlay_vs_no_graph_all": paired(rows, "overlay", "no_graph"),
            "overlay_vs_graph_rank_all": paired(rows, "overlay", "graph_rank"),
        },
        "uncertainty": {
            "overlay_wilson_95": wilson(sum(row["overlay_correct"] for row in rows), len(rows)),
            "tail_wilson_95": wilson(sum(row["tail_correct"] for row in rows), len(rows)),
            "novel_cluster_bootstrap_overlay_minus_tail_points_95": clustered_bootstrap(rows, "overlay", "tail"),
            "novel_cluster_bootstrap_overlay_minus_no_graph_points_95": clustered_bootstrap(rows, "overlay", "no_graph"),
        },
        "macro_accuracy": {
            method: sum(by_novel[novel][method]["accuracy"] for novel in by_novel) / len(by_novel)
            for method in ("overlay", "graph_rank", "no_graph", "tail", "question_only", "full_novel")
        },
        "retrieval": {
            "mean_unique_chunks": sum(row["retrieved_chunks"] for row in rows) / len(rows),
            "mean_retrieved_chars": sum(row["retrieved_chars"] for row in rows) / len(rows),
            "questions_with_graph_paths": sum(row["graph_path_count"] > 0 for row in rows),
            "mean_graph_paths": sum(row["graph_path_count"] for row in rows) / len(rows),
            "grounded_support_quotes": sum(row["grounded_support_quote"] for row in rows),
        },
        "by_novel": by_novel,
        "by_question_type": by_type,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUT / "per_question.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (int(row["novel"]), row["qi"])))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
