"""Forest plot of the key paired (McNemar) contrasts.

Horizontal error bars are the novel-clustered bootstrap 95% CIs on the accuracy
delta; markers are filled when the exact paired p-value < 0.05. Reads the
verified aggregation JSONs.
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
FAIR = json.loads((ROOT / "paper" / "generated" / "dqa30_fair_gold_results.json").read_text(encoding="utf-8"))
LATEST = json.loads((ROOT / "paper" / "generated" / "dqa30_latest30_results.json").read_text(encoding="utf-8"))

# (label, delta, ci, p, holm, group)
def row(label, delta, ci, p, holm, group):
    return {"label": label, "delta": delta, "ci": ci, "p": p, "holm": holm, "group": group}


def build() -> list[dict]:
    p = FAIR["paired"]
    pd_ = LATEST["descriptive30"]["paired_graph_vs_baselines"]
    pn = LATEST["new10"]["paired_graph_vs_baselines"]
    return [
        row("Fair gold vs. question-only (terse)", p["GOLD_V2_vs_Q0T"]["delta"] * 100,
            [c * 100 for c in p["GOLD_V2_vs_Q0T"]["novel_cluster_delta_95"]], p["GOLD_V2_vs_Q0T"]["exact_p"], None, "gold"),
        row("Graph evidence expansion vs. recent-window (pooled)", pd_["G7_vs_B1"]["delta"] * 100,
            [c * 100 for c in pd_["G7_vs_B1"]["novel_cluster_delta_95"]], pd_["G7_vs_B1"]["exact_p"], pd_["G7_vs_B1"]["holm_p"], "graph"),
        row("Graph disagreement arbitration vs. recent-window (new10)", pn["G10_vs_B1"]["delta"] * 100,
            [c * 100 for c in pn["G10_vs_B1"]["novel_cluster_delta_95"]], pn["G10_vs_B1"]["exact_p"], pn["G10_vs_B1"]["holm_p"], "graph"),
        row("Fair gold vs. graph evidence expansion", p["GOLD_V2_vs_G7"]["delta"] * 100,
            [c * 100 for c in p["GOLD_V2_vs_G7"]["novel_cluster_delta_95"]], p["GOLD_V2_vs_G7"]["exact_p"], None, "gold"),
        row("Fair gold vs. graph disagreement arbitration", p["GOLD_V2_vs_G10"]["delta"] * 100,
            [c * 100 for c in p["GOLD_V2_vs_G10"]["novel_cluster_delta_95"]], p["GOLD_V2_vs_G10"]["exact_p"], None, "gold"),
        row("Options-first gold vs. question-only (artifact)", p["GOLD_ORIG_vs_Q0"]["delta"] * 100,
            [c * 100 for c in p["GOLD_ORIG_vs_Q0"]["novel_cluster_delta_95"]], p["GOLD_ORIG_vs_Q0"]["exact_p"], None, "artifact"),
    ]


GROUP_COLORS = {"gold": "#1f77b4", "graph": "#2ca02c", "artifact": "#d62728"}
GROUP_LABEL = {
    "gold": "oracle / control contrasts",
    "graph": "graph vs. recent-window baseline",
    "artifact": "options-position artifact (control)",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = build()  # order: bottom of plot = first row
    y = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(9.4, 3.9), constrained_layout=True)

    for yi, r in zip(y, rows):
        color = GROUP_COLORS[r["group"]]
        significant = r["p"] < 0.05
        lo, hi = r["ci"]
        ax.errorbar([r["delta"]], [yi], xerr=[[r["delta"] - lo], [hi - r["delta"]]],
                    fmt="o", markersize=7.5, color=color, ecolor=color, elinewidth=1.8, capsize=4,
                    markerfacecolor=color if significant else "white", markeredgecolor=color, markeredgewidth=1.4)
        label = f"{r['delta']:+.1f}pp"
        if r["p"] < 0.001:
            label += f", p<0.001"
        else:
            label += f", p={r['p']:.3f}"
        if r["holm"] is not None:
            label += f", Holm {r['holm']:.3f}"
        ax.text(hi + 1.2, yi, label, va="center", fontsize=7.8, color="#24303c")

    ax.axvline(0, color="#5b6b7f", linewidth=1.0, linestyle="-")
    ax.set_yticks(y, [r["label"] for r in rows], fontsize=9)
    ax.invert_yaxis()  # rows[0] (headline contrast) renders at the top
    ax.set_xlim(-12, 34)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlabel("paired accuracy delta (pp), exact two-sided McNemar", fontsize=9)
    ax.set_title("Paired contrasts with novel-clustered 95% CIs", loc="left", fontsize=10.5,
                 fontweight="bold", color="#1c2733")
    ax.grid(axis="x", color="#e0e5eb", linewidth=0.7)
    ax.spines[["top", "right"]].set_visible(False)
    handles = [plt.Line2D([0], [0], marker="o", color="none", markerfacecolor=c, markeredgecolor=c,
                          markersize=8, label=GROUP_LABEL[g]) for g, c in GROUP_COLORS.items()]
    filled = plt.Line2D([0], [0], marker="o", color="none", markerfacecolor="#333", markeredgecolor="#333",
                        markersize=8, label="p < 0.05")
    empty = plt.Line2D([0], [0], marker="o", color="none", markerfacecolor="white", markeredgecolor="#333",
                       markersize=8, label="n.s.")
    ax.legend(handles=[*handles, filled, empty], frameon=False, fontsize=7.6, loc="lower right", ncol=2)

    for suffix in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"fig_pairwise.{suffix}", dpi=300 if suffix == "png" else None, bbox_inches="tight")
    plt.close(fig)
    print("wrote fig_pairwise.{pdf,png,svg}")


if __name__ == "__main__":
    main()
