"""Aggregate the frozen old20 and new10 DQA evaluations for the paper.

The old20 graph predictions are reconstructed from the frozen pure-9B
permutation experiment; only B2/B3 come from the new missing-baselines run.
The new10 rows are read from the completed batch03 evaluation.  No model or
retriever is called by this script.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / "scripts").is_dir():
    ROOT = Path.cwd()
METHODS = ("G1", "G2", "G3", "G4", "G5", "B1", "B2", "B3", "Q0")
GRAPH_METHODS = METHODS[:5]
BASELINES = ("B1", "B2", "B3")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def wilson(correct: int, total: int) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    z = 1.959963984540054
    p = correct / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def exact_mcnemar(rows: list[dict[str, Any]], method: str, baseline: str) -> dict[str, Any]:
    wins = sum(row["correct"][method] and not row["correct"][baseline] for row in rows)
    losses = sum(not row["correct"][method] and row["correct"][baseline] for row in rows)
    discordant = wins + losses
    if discordant == 0:
        p = 1.0
    else:
        lower = min(wins, losses)
        p = min(1.0, 2 * sum(math.comb(discordant, index) for index in range(lower + 1)) / (2**discordant))
    return {"wins": wins, "losses": losses, "discordant": discordant, "exact_p": p}


def holm(entries: dict[str, dict[str, Any]]) -> None:
    ordered = sorted(entries.items(), key=lambda item: item[1]["exact_p"])
    adjusted = 0.0
    count = len(ordered)
    for rank, (key, item) in enumerate(ordered):
        adjusted = max(adjusted, min(1.0, item["exact_p"] * (count - rank)))
        entries[key]["holm_p"] = adjusted


def cluster_bootstrap(rows: list[dict[str, Any]], method: str, baseline: str | None = None, samples: int = 5000) -> list[float]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["novel"]].append(row)
    novels = sorted(grouped)
    rng = random.Random(20260824)
    values = []
    for _ in range(samples):
        sample = [row for _ in novels for row in grouped[rng.choice(novels)]]
        value = sum(row["correct"][method] for row in sample) / len(sample)
        if baseline:
            value -= sum(row["correct"][baseline] for row in sample) / len(sample)
        values.append(value)
    values.sort()
    return [values[int(0.025 * samples)], values[min(int(0.975 * samples), samples - 1)]]


def score(rows: list[dict[str, Any]], method: str) -> dict[str, Any]:
    correct = sum(row["correct"][method] for row in rows)
    per_novel = []
    for novel in sorted({row["novel"] for row in rows}, key=int):
        subset = [row for row in rows if row["novel"] == novel]
        per_novel.append(sum(row["correct"][method] for row in subset) / len(subset))
    return {
        "correct": correct,
        "total": len(rows),
        "micro_accuracy": correct / len(rows) if rows else 0.0,
        "macro_novel_accuracy": sum(per_novel) / len(per_novel) if per_novel else 0.0,
        "wilson_95": wilson(correct, len(rows)),
        "novel_cluster_bootstrap_95": cluster_bootstrap(rows, method),
    }


def load_old20(root: Path) -> list[dict[str, Any]]:
    graph_rows = read_csv(root / "outputs" / "four_datasets" / "dqa_local_c24_pure9_consensus20" / "per_question.csv")
    q0_rows = {(row["novel"], row["qid"]): row for row in read_csv(root / "outputs" / "four_datasets" / "dqa_local_c16_consensus20" / "per_question.csv")}
    baseline_root = root / "outputs" / "four_datasets" / "dqa30_frozen_old20_baselines9b" / "answers"
    rows = []
    for source in graph_rows:
        novel, qid, qi = source["novel"], source["qid"], int(source["qi"])
        path = baseline_root / novel / f"q{qi:02d}.json"
        if not path.is_file():
            raise FileNotFoundError(f"old20 baseline incomplete: {path}")
        base = json.loads(path.read_text(encoding="utf-8"))
        q0 = q0_rows[(novel, qid)]["closed35"]
        g1, g2, g3, tail = source["original"], source["reversed"], source["cyclic"], source["tail"]
        selected = {
            "G1": g1,
            "G2": g2,
            "G3": g3,
            "G4": g1 if g1 == g2 else tail,
            "G5": source["c24"],
            "B1": tail,
            "B2": base["answers"]["B2"]["selected_letter"],
            "B3": base["answers"]["B3"]["selected_letter"],
            "Q0": q0,
        }
        gold = source["gold"]
        rows.append(
            {
                "cohort": "old20",
                "novel": novel,
                "qi": qi,
                "qid": qid,
                "gold": gold,
                "selected": selected,
                "correct": {method: selected[method] == gold for method in METHODS},
                "graph_sha256": base["graph_sha256"],
                "source_signature": {
                    "graph_methods": "c24-pure-qwen35-9b-three-permutation-majority-tail-fallback-v1",
                    "q0": "dqa_local_c16_consensus20/closed35",
                    "baselines": base["version"],
                },
            }
        )
    return rows


def load_new10(root: Path) -> list[dict[str, Any]]:
    answer_root = root / "outputs" / "four_datasets" / "dqa30_attention" / "batch03_eval" / "answers"
    rows = []
    for path in sorted(answer_root.glob("*/q*.json"), key=lambda value: (int(value.parent.name), value.name)):
        item = json.loads(path.read_text(encoding="utf-8"))
        selected = {method: item["answers"][method]["selected_letter"] for method in METHODS}
        rows.append(
            {
                "cohort": "new10",
                "novel": item["novel"],
                "qi": item["qi"],
                "qid": item["qid"],
                "gold": item["gold_letter"],
                "selected": selected,
                "correct": {method: selected[method] == item["gold_letter"] for method in METHODS},
                "graph_sha256": item["graph_sha256"],
                "source_signature": {"all_methods": item["version"], "source_hash": item["source_hash"]},
            }
        )
    if len(rows) != 70:
        raise RuntimeError(f"expected 70 new10 rows, found {len(rows)}")
    return rows


def cohort_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hard = [row for row in rows if not row["correct"]["Q0"]]
    easy = [row for row in rows if row["correct"]["Q0"]]
    paired = {
        f"{method}_vs_{baseline}": exact_mcnemar(rows, method, baseline)
        for method in GRAPH_METHODS for baseline in BASELINES
    }
    for key, item in paired.items():
        method, baseline = key.split("_vs_")
        item["delta"] = score(rows, method)["micro_accuracy"] - score(rows, baseline)["micro_accuracy"]
        item["novel_cluster_delta_95"] = cluster_bootstrap(rows, method, baseline)
    holm(paired)
    return {
        "questions": len(rows),
        "novels": len({row["novel"] for row in rows}),
        "all": {method: score(rows, method) for method in METHODS},
        "q0_wrong": {method: score(hard, method) for method in METHODS if method != "Q0"},
        "q0_correct_preservation": {method: score(easy, method) for method in METHODS if method != "Q0"},
        "paired_graph_vs_baselines": paired,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=ROOT / "paper" / "generated")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    old = load_old20(ROOT)
    new = load_new10(ROOT)
    all_rows = old + new
    report = {
        "metadata": {
            "protocol": "dqa30-frozen-reuse-paper-v1",
            "single_answer_model": "qwen3.5:9b",
            "thinking": "disabled",
            "mask": "unmasked",
            "warning": "old20 and new10 use different graph construction versions; pooled30 is descriptive.",
            "exploratory_warning": "G1-G5 were developed before or during these cohorts; results are not a pristine blind test.",
        },
        "old20": cohort_report(old),
        "new10": cohort_report(new),
        "descriptive30": cohort_report(all_rows),
    }
    (args.out_dir / "dqa30_frozen_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.out_dir / "dqa30_per_question.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["cohort", "novel", "qi", "qid", "gold", *METHODS, *[f"correct_{method}" for method in METHODS], "graph_sha256", "source_signature"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in all_rows:
            writer.writerow(
                {
                    "cohort": row["cohort"], "novel": row["novel"], "qi": row["qi"], "qid": row["qid"], "gold": row["gold"],
                    **row["selected"], **{f"correct_{method}": row["correct"][method] for method in METHODS},
                    "graph_sha256": row["graph_sha256"], "source_signature": json.dumps(row["source_signature"], ensure_ascii=False),
                }
            )
    with (args.out_dir / "dqa30_method_summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["cohort", "subset", "method", "correct", "total", "micro_accuracy", "macro_novel_accuracy", "ci_low", "ci_high"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for cohort in ("old20", "new10", "descriptive30"):
            for subset in ("all", "q0_wrong", "q0_correct_preservation"):
                for method, item in report[cohort][subset].items():
                    writer.writerow(
                        {
                            "cohort": cohort, "subset": subset, "method": method, "correct": item["correct"], "total": item["total"],
                            "micro_accuracy": item["micro_accuracy"], "macro_novel_accuracy": item["macro_novel_accuracy"],
                            "ci_low": item["wilson_95"][0], "ci_high": item["wilson_95"][1],
                        }
                    )
    print(json.dumps({cohort: {method: block["micro_accuracy"] for method, block in report[cohort]["all"].items()} for cohort in report if cohort != "metadata"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
