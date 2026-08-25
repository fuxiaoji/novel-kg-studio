"""Conceptual figure: linear text history (0--1 ruler) vs non-linear graph navigation.

Pure matplotlib -- no data files, no LLM. Two panels:
  (top)    a 0--1 linear reading ruler with a recent-context window and a
           supporting clue that sits far behind the window (linearly out of reach);
  (bottom) a mind-map graph where the same clue is one hop from the question node.
Hop depth follows the dashboard metaphor: 1st hop saturated red, 2nd hop orange,
filler gray; the clue node carries a gold ring.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper" / "neurips2026" / "figures"

# dashboard-derived palette
GOLD = "#ffd700"
HOP1 = "#d62728"      # 1st hop, saturated
HOP2 = "#ff7f0e"      # 2nd hop, lighter
FILLER = "#cccccc"    # unrelated filler nodes
QUERY = "#182333"     # question node
EDGE_MAIN = "#8a94a3"
EDGE_DIM = "#c4cad3"
WINDOW_FACE = "#5b6b7f"


def _ruler(ax) -> None:
    """Linear 0--1 reading ruler with a recent-context window and an out-of-reach clue."""
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    # gradient strip (light at 0 -> dark at 1) drawn as thin vertical slabs
    for i in range(200):
        x = i / 200
        ax.axvspan(x, x + 1 / 200, color=(0.30 + 0.42 * x, 0.34 + 0.42 * x, 0.42 + 0.40 * x), lw=0)

    # recent-context reading window on the tail
    ax.add_patch(plt.Rectangle((0.72, 0.16), 0.28, 0.30, facecolor=WINDOW_FACE, edgecolor="#2f3d4d",
                               hatch="////", alpha=0.72, linewidth=1.1, zorder=3))
    ax.text(0.86, 0.435, "linear reading window\n(recent context)", ha="center", va="bottom",
            fontsize=9.5, color="#1c2733", fontweight="bold", zorder=4)

    # the supporting clue: buried at position 0.28, far behind the window
    ax.plot([0.28], [0.31], marker="o", markersize=13, color=GOLD, markeredgecolor="#7a5c00",
            markeredgewidth=1.6, zorder=6)
    ax.annotate(
        "supporting clue\n(linear position 0.28)",
        xy=(0.28, 0.31), xytext=(0.06, 0.78),
        arrowprops=dict(arrowstyle="->", color="#33404d", lw=1.3),
        fontsize=9.5, color="#1c2733", ha="left", va="center", fontweight="bold",
    )
    # show the reading window cannot reach back there
    ax.annotate(
        "",
        xy=(0.28, 0.40), xytext=(0.72, 0.40),
        arrowprops=dict(arrowstyle="-|>", color="#a34a4a", lw=1.5, ls=(0, (5, 3))),
    )
    ax.text(0.50, 0.55, "linearly out of reach inside the window", ha="center", va="bottom",
            fontsize=8.8, color="#a34a4a", style="italic")

    # axis ticks 0 / 0.5 / 1
    ax.set_xticks([0.0, 0.5, 1.0])
    ax.set_xticklabels(["beginning", "middle", "end"], fontsize=9)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#5b6b7f")
    ax.set_yticks([])
    ax.set_ylim(0.0, 1.0)
    ax.tick_params(axis="x", length=0)
    ax.text(0.0, 0.90, "Linear input history: a token-by-token reading window", fontsize=10.5,
            fontweight="bold", color="#1c2733")


def _mind_map(ax) -> None:
    """Non-linear graph navigation: question node, 1st/2nd hops, gold-ringed clue."""
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")

    center = (0.5, 0.46)
    ax.plot([center[0]], [center[1]], marker="o", markersize=34, color=QUERY, zorder=5)
    ax.text(center[0], center[1] + 0.025, "question", ha="center", va="center",
            fontsize=10.5, color="white", fontweight="bold", zorder=6)

    # layer-1 hops (saturated red, dashboard size 12)
    layer1 = [(0.24, 0.78), (0.80, 0.74), (0.30, 0.16), (0.84, 0.18), (0.10, 0.45)]
    labels1 = ["protagonist", "suspect", "crime scene", "motive", "witness"]
    gold_idx = 1  # the suspect node doubles as the evidence bridge
    for i, (x, y) in enumerate(layer1):
        ax.plot([center[0], x], [center[1], y], color=EDGE_MAIN, lw=1.5, zorder=1)
        ax.plot([x], [y], marker="o", markersize=12, color=HOP1,
                markeredgecolor="white", markeredgewidth=1.0, zorder=3)
        dx = 0.10 if x >= center[0] else -0.10
        ax.text(x + dx, y, labels1[i], ha="left" if dx > 0 else "right", va="center",
                fontsize=8.5, color="#24303c", fontweight="bold", zorder=4)

    # layer-2 hops (orange, dashboard size 8), connected sparsely
    layer2 = [(0.05, 0.80), (0.55, 0.90), (0.97, 0.86), (0.60, 0.03), (0.97, 0.05), (0.03, 0.10)]
    parents = [0, 0, 1, 3, 4, 2]
    for i, (x, y) in enumerate(layer2):
        px, py = layer1[parents[i]]
        ax.plot([px, x], [py, y], color=EDGE_DIM, lw=1.0, zorder=1)
        ax.plot([x], [y], marker="o", markersize=8, color=HOP2,
                markeredgecolor="white", markeredgewidth=0.8, zorder=3)

    # a couple of filler nodes unrelated to the answer (gray)
    for (x, y) in [(0.44, 0.78), (0.92, 0.42), (0.08, 0.22)]:
        ax.plot([x], [y], marker="o", markersize=7, color=FILLER,
                markeredgecolor="white", markeredgewidth=0.7, zorder=3)

    # the supporting clue: one hop from the question, gold ringed
    clue = (0.80, 0.74)  # == layer1[1]
    ax.plot([clue[0]], [clue[1]], marker="o", markersize=15, color=HOP1,
            markeredgecolor=GOLD, markeredgewidth=2.6, zorder=6)
    ax.annotate(
        "the same supporting clue\n(reachable in one hop)",
        xy=(clue[0] + 0.02, clue[1] + 0.01), xytext=(0.72, 0.98),
        arrowprops=dict(arrowstyle="->", color="#7a5c00", lw=1.4),
        fontsize=8.8, color="#5f4700", ha="left", va="center", fontweight="bold",
    )

    ax.text(0.0, 0.96, "Non-linear graph navigation: one hop from the question", fontsize=10.5,
            fontweight="bold", color="#1c2733")
    ax.text(0.0, -0.02, "hop depth: 1st hop (saturated)  /  2nd hop (lighter)  /  unrelated (gray); "
            "the gold ring marks the evidence that linear history cannot reach",
            fontsize=7.8, color="#4f5b6b")
    ax.axis("off")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.6, 7.6), constrained_layout=True)
    _ruler(ax1)
    _mind_map(ax2)
    for suffix in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"fig_schematic_linear_vs_graph.{suffix}", dpi=300 if suffix == "png" else None,
                    bbox_inches="tight")
    plt.close(fig)
    print("wrote fig_schematic_linear_vs_graph.{pdf,png,svg}")


if __name__ == "__main__":
    main()
