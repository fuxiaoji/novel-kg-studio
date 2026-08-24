"""Statistical audit of the graph-vs-compression causal attribution proxy."""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "attention_proxy_v2" / "pilot.json"
OUT = ROOT / "reports" / "DQA30_ATTENTION_PROXY_ANALYSIS_20260824.json"
LETTERS = "ABCD"


def metrics(rows):
    graph = [r["graph_gold_logprob_delta"] for r in rows]
    compression = [r["compression_gold_logprob_delta"] for r in rows]
    differences = [g - c for g, c in zip(graph, compression)]
    graph_accuracy = sum(max(r["scores"]["graph_full"]["normalized_probs"], key=r["scores"]["graph_full"]["normalized_probs"].get) == r["gold"] for r in rows) / len(rows)
    compression_accuracy = sum(max(r["scores"]["compression_full"]["normalized_probs"], key=r["scores"]["compression_full"]["normalized_probs"].get) == r["gold"] for r in rows) / len(rows)
    return {
        "n": len(rows),
        "graph": {"mean_delta": statistics.fmean(graph), "median_delta": statistics.median(graph), "positive_fraction": sum(x > 0 for x in graph) / len(graph), "argmax_accuracy": graph_accuracy},
        "compression": {"mean_delta": statistics.fmean(compression), "median_delta": statistics.median(compression), "positive_fraction": sum(x > 0 for x in compression) / len(compression), "argmax_accuracy": compression_accuracy},
        "paired_graph_minus_compression": {"mean": statistics.fmean(differences), "median": statistics.median(differences), "positive_fraction": sum(x > 0 for x in differences) / len(differences)},
    }


def cluster_bootstrap(rows, iterations=20000):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["novel"]].append(row)
    novels = sorted(grouped, key=int)
    rng = random.Random(20260824)
    values = []
    for _ in range(iterations):
        sampled = [row for novel in rng.choices(novels, k=len(novels)) for row in grouped[novel]]
        values.append(statistics.fmean(row["graph_gold_logprob_delta"] - row["compression_gold_logprob_delta"] for row in sampled))
    values.sort()
    return [values[int(0.025 * iterations)], values[int(0.975 * iterations) - 1]]


def main():
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = data["rows"]
    retained = [row for row in rows if row["graph_gold_retained"]]
    missed = [row for row in rows if not row["graph_gold_retained"]]
    thresholds = {}
    for threshold in (0.45, 0.50, 0.55, 0.60, 0.65, 0.70):
        thresholds[str(threshold)] = sum(any(a["similarity"] >= threshold for a in row["compression_alignments"]) for row in rows) / len(rows)
    report = {
        "metadata": data["metadata"],
        "interpretation_guard": "Causal option-logprob attribution proxy; not an internal attention tensor.",
        "all70": metrics(rows),
        "graph_gold_retained": metrics(retained),
        "graph_gold_missed": metrics(missed),
        "graph_retention": len(retained) / len(rows),
        "compression_retention_sensitivity": thresholds,
        "cluster_bootstrap95_graph_minus_compression": cluster_bootstrap(rows),
        "per_novel": {novel: metrics([row for row in rows if row["novel"] == novel]) for novel in sorted({row["novel"] for row in rows}, key=int)},
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
