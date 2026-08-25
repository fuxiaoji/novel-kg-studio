"""Reuse two existing frozen-artifact figures for the NeurIPS draft:

  fig_force_novel103.pdf   -- frozen Novel 103 force layout with gold/answer evidence
                              (via plot_dqa30_paper_figures.force_figure)
  fig_core_enrichment.pdf  -- gold-overlap node concentration in graph 2-cores
                              (re-plotted from the verified dense-regions JSON)

No LLM calls, no graph rewrites.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper" / "neurips2026" / "figures"

from plot_dqa30_paper_figures import force_figure  # noqa: E402


def core_enrichment() -> None:
    report = json.loads((ROOT / "paper" / "generated" / "dqa30_gold_dense_regions.json").read_text(encoding="utf-8"))
    cohorts = [
        ("Old 20", "old20"),
        ("New 10", "new10"),
        ("All 30\n(descriptive)", "descriptive30"),
    ]
    gold = [report["summary"][key]["core2"]["gold_rate_micro"] for _, key in cohorts]
    nongold = [report["summary"][key]["core2"]["nongold_rate_micro"] for _, key in cohorts]
    enrich = [report["summary"][key]["core2"]["enrichment_ratio"] for _, key in cohorts]

    x = np.arange(len(cohorts))
    width = 0.34
    fig, ax = plt.subplots(figsize=(7.2, 4.0), constrained_layout=True)
    ax.bar(x - width / 2, gold, width, label="Gold-overlap nodes", color="#e76f51")
    ax.bar(x + width / 2, nongold, width, label="Other nodes", color="#8da9c4")
    ax.set_xticks(x, [name for name, _ in cohorts])
    ax.set_ylim(0, 1.04)
    ax.set_ylabel("Proportion of nodes in graph 2-core", fontsize=9)
    ax.set_title("Gold-evidence nodes are concentrated in topological cores", loc="left",
                 fontsize=10.5, fontweight="bold", color="#1c2733")
    ax.grid(axis="y", color="#dde3ea", linewidth=0.7, alpha=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, ncol=2, loc="upper center", fontsize=8)
    for px, value in zip(x - width / 2, gold):
        ax.text(px, value + 0.02, f"{value:.0%}", ha="center", va="bottom", fontsize=8.5)
    for px, value in zip(x + width / 2, nongold):
        ax.text(px, value + 0.02, f"{value:.0%}", ha="center", va="bottom", fontsize=8.5)
    for px, value, e in zip(x, gold, enrich):
        ax.text(px, value + 0.085, f"×{e:.1f}", ha="center", va="bottom", fontsize=10,
                fontweight="bold", color="#5f1d12")
    for suffix in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"fig_core_enrichment.{suffix}", dpi=300 if suffix == "png" else None, bbox_inches="tight")
    plt.close(fig)
    print("wrote fig_core_enrichment.{pdf,png,svg}")


def force() -> None:
    manifest = ROOT / "config" / "dqa30_frozen_graphs.json"
    audit = ROOT / "paper" / "generated" / "dqa30_gold_node_audit.csv"
    frozen = json.loads(manifest.read_text(encoding="utf-8"))
    # force_figure expects a file whose top-level key is `novels`; adapt in place.
    adapted = OUT / ".manifest_adapter.json"
    adapted.write_text(json.dumps({"novels": frozen["records"]}), encoding="utf-8")
    force_figure(adapted, audit, OUT)
    adapted.unlink(missing_ok=True)
    for suffix in ("png", "svg"):
        (OUT / f"force_graph_novel103_paper.{suffix}").unlink(missing_ok=True)
    (OUT / "fig_force_novel103.pdf").unlink(missing_ok=True)
    (OUT / "force_graph_novel103_paper.pdf").rename(OUT / "fig_force_novel103.pdf")
    print("wrote fig_force_novel103.pdf")


if __name__ == "__main__":
    core_enrichment()
    force()
