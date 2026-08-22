"""Render a static preview of the one-dimensional time-scale ruler (PNG)."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
import math

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"
DAY_COLORS = {1: "#4e79a7", 2: "#f28e2b", 3: "#e15759", 4: "#59a14f"}


def main() -> None:
    rows = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = sorted(rows, key=lambda r: r["text_position"])
    xs = np.array([float(r["text_position"]) for r in rows])
    period_hour = {"morning": 8, "noon": 12, "afternoon": 15, "evening": 19, "night": 22, "unknown": 12}
    period_dur = {"morning": 4, "noon": 2, "afternoon": 4, "evening": 3, "night": 2, "unknown": 8}
    groups: dict[tuple, list] = {}
    for r in rows:
        day = int(r["day"]) if r.get("day") is not None else int(round(float(r.get("day_filled", 1))))
        groups.setdefault((day, r.get("period", "unknown")), []).append(r)
    hours = np.zeros(len(rows))
    for (day, period), group in groups.items():
        base = day * 24 + period_hour.get(period, 12)
        dur = period_dur.get(period, 4)
        ordered = sorted(group, key=lambda r: r["text_position"])
        first = ordered[0]["text_position"]
        last = ordered[-1]["text_position"]
        for k, r in enumerate(ordered):
            frac = (r["text_position"] - first) / (last - first) if len(ordered) > 1 and last > first else 0.5
            hours[rows.index(r)] = base + frac * dur
    hours = np.maximum.accumulate(hours)
    h_min = math.floor(float(hours.min()))

    bins = 160
    centers = (np.arange(bins) + 0.5) / bins
    z = np.interp(centers, xs, hours)

    fig, ax = plt.subplots(figsize=(11, 2.7), dpi=110)
    ax.scatter(centers, np.zeros(bins), c=z, cmap="viridis", s=90, marker="s")
    for hour in range(math.floor(float(hours.min())), math.ceil(float(hours.max())) + 1):
        if hour < hours.min() or hour > hours.max():
            continue
        pos = float(np.interp(hour, hours, xs))
        ax.plot([pos, pos], [-0.18, 0.18], color="#333", lw=0.8)
        rel = hour - h_min
        if rel == 0 or rel % 6 == 0:
            ax.text(pos, -0.3, f"第{rel}小时", fontsize=8, rotation=90, ha="center", va="top")
    for day in sorted({int(round(float(r.get("day_filled", 1)))) for r in rows}):
        pos = float(np.interp(day * 24, hours, xs))
        ax.axvline(pos, color="#666", lw=0.9, ls="--")
        ax.text(pos, 0.95, f"Day {day}", fontsize=9, ha="center", color="#555")
    ax.set_xlim(0, 1)
    ax.set_ylim(-0.75, 1.15)
    ax.set_yticks([])
    ax.set_xlabel("文本位置（0–1 米尺）")
    ax.set_title("时间刻度表在文本尺（0–1）上的分布（小时为最小单位）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = OUT / "time_over_text_preview.png"
    fig.savefig(path)
    print(path)
    print("first hour tick positions on the text ruler:")
    for hour in range(math.floor(float(hours.min())), math.floor(float(hours.min())) + 10):
        if hour > hours.max():
            break
        pos = float(np.interp(hour, hours, xs))
        print(f"  第{hour - h_min}小时 -> 文本位置 {pos:.4f} ({pos * 100:.2f}%)")


if __name__ == "__main__":
    main()
