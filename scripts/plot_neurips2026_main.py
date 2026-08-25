"""Main accuracy figure: three panels (old20 / new10 / pooled) of grouped bars
for the seven non-oracle methods, with a dashed gold-ceiling reference line in
each panel drawn from the de-biased gold oracle.

Reads paper/generated/dqa30_fair_gold_results.json (verified aggregation).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper" / "neurips2026" / "figures"
FAIR = json.loads((ROOT / "paper" / "generated" / "dqa30_fair_gold_results.json").read_text(encoding="utf-8"))

# method (internal) -> paper label, fixed categorical color order (colorblind-aware)
METHODS = [
    ("G7", "Graph evidence\nexpansion", "#1f77b4"),
    ("G9", "Graph chunk\nreranking", "#ff7f0e"),
    ("G10", "Graph\ndisagreement\narbitration", "#2ca02c"),
    ("B1", "Recent-window\nbaseline", "#d62728"),
    ("B2", "Whole-book\ncompression", "#9467bd"),
    ("B3", "Vector RAG\nbaseline", "#8c564b"),
    ("Q0", "Question-only\ncontrol", "#7f7f7f"),
]
COHORTS = [("old20", "Old 20"), ("new10", "New 10"), ("pooled", "All 30 (descriptive)")]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(10.6, 3.9), constrained_layout=True, sharey=True)

    for ax, (cohort, title) in zip(axes, COHORTS):
        block = FAIR[cohort]
        values = [block[m]["micro"] * 100 for m, _, _ in METHODS]
        colors = [c for _, _, c in METHODS]
        gold = block["GOLD_V2"]["micro"] * 100

        x = np.arange(len(METHODS))
        bars = ax.bar(x, values, 0.62, color=colors, alpha=0.92, edgecolor="white", linewidth=0.5)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, value + 0.7, f"{value:.1f}", ha="center",
                    va="bottom", fontsize=7.6, color="#24303c")
            if value > gold:
                ax.text(bar.get_x() + bar.get_width() / 2, value + 2.1, "*", ha="center",
                        va="bottom", fontsize=11, color="#b00", fontweight="bold")

        # gold ceiling reference line
        ax.axhline(gold, color="#d4af37", linewidth=1.8, linestyle=(0, (6, 3)))
        ax.text(len(METHODS) - 0.5, gold + 1.2, f"de-biased oracle {gold:.1f}%", ha="right",
                va="bottom", fontsize=7.8, color="#8a6d00", fontweight="bold")

        ax.set_xticks(x, [label for _, label, _ in METHODS], fontsize=7.4)
        ax.set_ylim(0, 70)
        ax.set_title(title, loc="left", fontsize=10.5, fontweight="bold", color="#1c2733")
        ax.grid(axis="y", color="#e0e5eb", linewidth=0.7)
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(axis="y", labelsize=8)
        if cohort == "old20":
            ax.set_ylabel("multiple-choice accuracy (%)", fontsize=9)

    fig.suptitle("", fontsize=1)
    note = ("Dashed line: de-biased oracle with all gold evidence, evidence-first and per-question shuffled options. "
            "Graph methods run with the original option order; where a bar exceeds the oracle line (marked *), the "
            "original option order grants the model an option-position advantage that the shuffled-option oracle "
            "removes (Section 5.3). Pooled 30 is descriptive across two graph-build cohorts.")
    fig.text(0.01, -0.01, note, fontsize=7.6, color="#4f5b6b")
    for suffix in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"fig_main_accuracy.{suffix}", dpi=300 if suffix == "png" else None, bbox_inches="tight")
    plt.close(fig)
    print("wrote fig_main_accuracy.{pdf,png,svg}")


if __name__ == "__main__":
    main()
