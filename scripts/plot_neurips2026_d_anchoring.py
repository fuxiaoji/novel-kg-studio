"""D-anchoring artifact figure.

Left panel : per-gold-letter accuracy (A/B/C/D) for the fair gold oracle, the
             question-only control (terse), and the options-first gold oracle
             (shows the 79.4% D spike).
Right panel: selected-letter distribution (share of the 234 questions) for the
             same runs plus the content-implied gold-letter distribution; the
             options-first oracle piles onto D (~59%) regardless of content.

Reads paper/generated/dqa30_fair_gold_results.json and the frozen answer dirs.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper" / "neurips2026" / "figures"
BASE = ROOT / "outputs" / "four_datasets" / "dqa30_attention"
FAIR = json.loads((ROOT / "paper" / "generated" / "dqa30_fair_gold_results.json").read_text(encoding="utf-8"))

GOLD_TRUTH = {"A": 58, "B": 53, "C": 58, "D": 65}


def load_selected(path: Path) -> Counter:
    counts: Counter = Counter()
    for item_file in path.rglob("q*.json"):
        item = json.loads(item_file.read_text(encoding="utf-8"))
        letter = item.get("selected_letter")
        if isinstance(letter, str) and letter in "ABCD":
            counts[letter] += 1
    return counts


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    per = FAIR["per_gold_letter"]
    runs = [
        ("Fair gold oracle", "GOLD_V2", "#1f77b4"),
        ("Question-only (terse)", "Q0T", "#7f7f7f"),
        ("Options-first gold oracle", "GOLD_ORIG", "#d62728"),
    ]
    letters = list("ABCD")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 3.9), constrained_layout=True, sharey=False)

    # ---- left: per-letter accuracy ----
    width = 0.26
    x = np.arange(len(letters))
    for i, (label, key, color) in enumerate(runs):
        vals = [per[key][k]["acc"] * 100 for k in letters]
        bars = ax1.bar(x + (i - 1) * width, vals, width, label=label, color=color,
                       alpha=0.92, edgecolor="white", linewidth=0.5)
        for bar, value in zip(bars, vals):
            if not (label == "Options-first gold oracle" and np.isclose(value, max(vals))):
                ax1.text(bar.get_x() + bar.get_width() / 2, value + 1.0, f"{value:.0f}", ha="center",
                         va="bottom", fontsize=7.4, color="#24303c")
            else:
                ax1.text(bar.get_x() + bar.get_width() / 2, value + 1.0, f"{value:.0f}", ha="center",
                         va="bottom", fontsize=8.2, color="#b00", fontweight="bold")
    ax1.set_xticks(x, letters, fontsize=10)
    ax1.set_ylim(0, 100)
    ax1.set_xlabel("content-implied gold letter", fontsize=9)
    ax1.set_ylabel("accuracy (%)", fontsize=9)
    ax1.set_title("Per-gold-letter accuracy", loc="left", fontsize=10.5, fontweight="bold", color="#1c2733")
    ax1.grid(axis="y", color="#e0e5eb", linewidth=0.7)
    ax1.spines[["top", "right"]].set_visible(False)
    ax1.legend(frameon=False, fontsize=7.6, loc="upper center", ncol=3)

    # ---- right: selected-letter distribution ----
    dists = []
    for label, key, _ in runs:
        counts = load_selected(BASE / "goldonly_9b_30" / "answers") if key == "GOLD_ORIG" \
            else load_selected(BASE / "q0_terse_30" / "answers") if key == "Q0T" \
            else load_selected(BASE / "goldonly_9b_30_fair" / "v2_evid_first_shuf" / "answers")
        dists.append([counts[k] / 234 * 100 for k in letters])  # share of all 234 questions
    gold_total = sum(GOLD_TRUTH.values())

    w2 = 0.19
    x2 = np.arange(len(letters))
    for i, (label, _key, color) in enumerate(runs):
        ax2.bar(x2 + (i - 1.5) * w2, dists[i], w2, label=label, color=color,
                alpha=0.92, edgecolor="white", linewidth=0.5)
    ax2.plot(x2 + 1.5 * w2, [GOLD_TRUTH[k] / gold_total * 100 for k in letters],
             marker="D", markersize=6, color="#d4af37", linestyle="none", label="gold-letter distribution")
    ax2.set_xticks(x2, letters, fontsize=10)
    ax2.set_ylim(0, 70)
    ax2.set_xlabel("selected letter", fontsize=9)
    ax2.set_ylabel("share of 234 questions (%)", fontsize=9)
    ax2.set_title("Selected-letter distribution", loc="left", fontsize=10.5, fontweight="bold", color="#1c2733")
    ax2.grid(axis="y", color="#e0e5eb", linewidth=0.7)
    ax2.spines[["top", "right"]].set_visible(False)
    ax2.legend(frameon=False, fontsize=7.6, loc="upper right", ncol=2)

    note = ("Options-first presentation lets option position (D) anchor the answer: the options-first gold oracle "
            "piles onto D (~59% of questions) and gets D right 79% of the time, while its A/B/C accuracy "
            "collapses. De-biasing (evidence first, shuffled options) restores per-letter balance.")
    fig.text(0.01, -0.02, note, fontsize=7.6, color="#4f5b6b")
    for suffix in ("pdf", "png", "svg"):
        fig.savefig(OUT / f"fig_d_anchoring.{suffix}", dpi=300 if suffix == "png" else None, bbox_inches="tight")
    plt.close(fig)
    print("wrote fig_d_anchoring.{pdf,png,svg}")


if __name__ == "__main__":
    main()
