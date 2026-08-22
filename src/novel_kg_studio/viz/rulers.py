from __future__ import annotations

import math
from typing import Any

import numpy as np
import plotly.graph_objects as go

DAY_COLORS = {1: "#4e79a7", 2: "#f28e2b", 3: "#e15759", 4: "#59a14f", 5: "#b6992d", 6: "#b07aa1"}
PERIOD_HOUR = {"morning": 8, "noon": 12, "afternoon": 15, "evening": 19, "night": 22, "unknown": 12}
PERIOD_DURATION = {"morning": 4, "noon": 2, "afternoon": 4, "evening": 3, "night": 2, "unknown": 8}


def _day_of(span: Any) -> int | None:
    if span.day is not None:
        return int(span.day)
    day_filled = getattr(span, "day_filled", None)
    if day_filled is not None and day_filled <= 10:
        return int(round(float(day_filled)))
    return None


def _moving_average(values: np.ndarray, k: int = 5) -> np.ndarray:
    out = np.zeros_like(values, dtype=float)
    for i in range(len(values)):
        lo = max(0, i - k // 2)
        hi = min(len(values), i + k // 2 + 1)
        out[i] = values[lo:hi].mean()
    return out


def deletion_ruler(kept_spans: list[Any], dropped_spans: list[Any], novel_len: int) -> go.Figure:
    """Step-1 ruler: per chunk, kept (green) vs dropped (red) segments over text position 0..1."""
    chunk_ids = sorted({s.chunk_idx for s in kept_spans} | {d.chunk_idx for d in dropped_spans})
    xs_base: list[float] = []
    ys_base: list[float] = []
    for c in chunk_ids:
        xs_base += [0.0, 1.0, None]
        ys_base += [c, c, None]
    base = go.Scatter(
        x=xs_base,
        y=ys_base,
        mode="lines",
        line=dict(color="#e0e0e0", width=10),
        hoverinfo="skip",
        showlegend=False,
    )
    traces = [base]

    def segments(spans: list[Any], color: str, name: str) -> None:
        xs: list[float] = []
        ys: list[float] = []
        hx: list[float] = []
        hy: list[float] = []
        htext: list[str] = []
        for s in spans:
            x0 = s.char_start / max(novel_len, 1)
            x1 = max(x0, s.char_end / max(novel_len, 1))
            xs += [x0, x1, None]
            ys += [s.chunk_idx, s.chunk_idx, None]
            hx.append((x0 + x1) / 2)
            hy.append(s.chunk_idx)
            htext.append(f"{name}: {s.text[:100]}")
        traces.append(
            go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=color, width=6), hoverinfo="skip", name=name)
        )
        traces.append(
            go.Scatter(
                x=hx,
                y=hy,
                mode="markers",
                marker=dict(size=1, opacity=0),
                hoverinfo="text",
                hovertext=htext,
                showlegend=False,
            )
        )

    segments(kept_spans, "#2e7d32", "保留")
    segments(dropped_spans, "#c62828", "删除")
    fig = go.Figure(data=traces)
    fig.update_layout(
        title="第一步：保留（绿）vs 删除（红）——按文本位置尺（0–1）",
        xaxis_title="文本位置（0–1）",
        yaxis_title="chunk 序号",
        height=600,
        hovermode="closest",
    )
    return fig


def dropped_reason_bar(stats: dict[str, Any]) -> go.Figure:
    reasons = dict(stats.get("dropped_by_reason") or {})
    fig = go.Figure(
        go.Bar(x=list(reasons.keys()), y=list(reasons.values()), marker_color="#c62828", name="删除字符数")
    )
    fig.update_layout(
        title="第一步：删除量按原因分类",
        xaxis_title="原因",
        yaxis_title="字符数",
        height=320,
    )
    return fig


def text_ruler_density(kept_spans: list[Any], bins: int = 100) -> go.Figure:
    """Events density over the text-position ruler (0..1)."""
    xs = np.array([s.text_position for s in kept_spans], dtype=float)
    weights = np.array([max(len(s.text), 1) for s in kept_spans], dtype=float)
    hist, edges = np.histogram(xs, bins=bins, range=(0.0, 1.0), weights=weights)
    centers = (edges[:-1] + edges[1:]) / 2
    fig = go.Figure()
    fig.add_bar(x=centers, y=hist, name="事件量（字符加权）", marker_color="#4e79a7")
    fig.add_scatter(x=centers, y=_moving_average(hist, 7), mode="lines", name="平滑", line=dict(color="#c62828", width=2))
    mean = float(hist.mean()) if hist.size else 0.0
    sparse = [i for i, h in enumerate(hist) if 0 < h < 0.3 * mean]
    for i in sparse[:3]:
        fig.add_annotation(
            x=centers[i],
            y=float(hist[i]) + 0.03 * float(hist.max() if hist.size else 1),
            text="稀疏区",
            showarrow=True,
            arrowhead=2,
        )
    fig.update_layout(
        title="事件在文本尺（0–1 米）上的分布——稀疏区标注",
        xaxis_title="文本位置（0–1）",
        yaxis_title="事件字符量",
        height=360,
        bargap=0.05,
    )
    return fig


def time_ruler_density(nodes: list[dict[str, Any]], bins: int = 60) -> go.Figure:
    """Clue-node density over the novel-time ruler (0..1)."""
    xs = np.array([float(n.get("time_pos", 0.5)) for n in nodes], dtype=float)
    hist, edges = np.histogram(xs, bins=bins, range=(0.0, 1.0))
    centers = (edges[:-1] + edges[1:]) / 2
    fig = go.Figure()
    fig.add_bar(x=centers, y=hist, name="线索节点数", marker_color="#f28e2b")
    fig.add_scatter(x=centers, y=_moving_average(hist.astype(float), 5), mode="lines", name="平滑", line=dict(color="#d62728", width=2))
    mean = float(hist.mean()) if hist.size else 0.0
    dense = [i for i, h in enumerate(hist) if h >= max(1.5 * mean, 1.0)]
    for i in dense[:5]:
        fig.add_annotation(
            x=centers[i],
            y=float(hist[i]) + 0.05 * float(hist.max() if hist.size else 1),
            text="稠密区",
            showarrow=True,
            arrowhead=2,
        )
    fig.update_layout(
        title="线索节点在小说时间尺（0–1）上的分布——稠密区标注",
        xaxis_title="小说时间（0–1）",
        yaxis_title="线索节点数",
        height=360,
        bargap=0.05,
    )
    return fig


def time_over_text_ruler(kept_spans: list[Any], bins: int = 60) -> go.Figure:
    """One-dimensional time scale laid on the text ruler (0..1), hour as minimum unit."""
    if not kept_spans:
        fig = go.Figure()
        fig.update_layout(title="时间刻度表在文本尺（0–1）上的分布", height=300)
        return fig
    spans = sorted(kept_spans, key=lambda s: s.text_position)
    hours = _story_hours(spans)
    hours = np.maximum.accumulate(hours)
    xs = np.array([s.text_position for s in spans], dtype=float)
    h_min = float(hours.min())
    h_max = float(hours.max())

    centers = (np.arange(bins) + 0.5) / bins
    z = np.interp(centers, xs, hours)
    hover = [f"文本位置={x:.3f}<br>故事时间=第{z[i]:.1f}小时" for i, x in enumerate(centers)]
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=centers,
            y=[0.0] * bins,
            mode="markers",
            marker=dict(
                symbol="square",
                size=16,
                color=z,
                colorscale="Viridis",
                showscale=True,
                colorbar=dict(title="故事小时"),
                line=dict(width=0.5, color="#444"),
            ),
            text=hover,
            hoverinfo="text",
            showlegend=False,
        )
    )

    tick_x: list[float] = []
    tick_rel: list[int] = []
    for hour in range(math.floor(h_min), math.ceil(h_max) + 1):
        if hour < h_min or hour > h_max:
            continue
        tick_x.append(float(np.interp(hour, hours, xs)))
        tick_rel.append(hour - math.floor(h_min))
    fig.add_trace(
        go.Scatter(
            x=tick_x,
            y=[0.0] * len(tick_x),
            mode="markers",
            marker=dict(symbol="line-ns", size=22, color="#333333"),
            hoverinfo="skip",
            showlegend=False,
        )
    )
    label_x: list[float] = []
    label_text: list[str] = []
    for pos, rel in zip(tick_x, tick_rel):
        if rel == 0 or rel % 6 == 0 or pos == tick_x[-1]:
            label_x.append(pos)
            label_text.append(f"第{rel}小时")
    fig.add_trace(
        go.Scatter(
            x=label_x,
            y=[-0.14] * len(label_x),
            mode="text",
            text=label_text,
            textfont=dict(size=9, color="#333333"),
            textposition="bottom center",
            hoverinfo="skip",
            showlegend=False,
        )
    )
    for day in sorted({int(round(s.day_filled)) for s in spans}):
        pos = float(np.interp(day * 24, hours, xs))
        fig.add_shape(type="line", x0=pos, x1=pos, y0=-0.24, y1=0.42, line=dict(color="#666666", width=1, dash="dash"))
        fig.add_annotation(x=pos, y=0.5, text=f"Day {day}", showarrow=False, font=dict(size=10, color="#555555"))
    fig.update_layout(
        title="时间刻度表在文本尺（0–1）上的分布（小时为最小单位）",
        xaxis_title="文本位置（0–1 米尺）",
        yaxis=dict(visible=False, range=[-0.45, 0.75]),
        height=330,
        margin=dict(l=50, r=30, t=60, b=60),
        hovermode="closest",
    )
    fig.update_xaxes(range=[0, 1])
    return fig


def _story_hours(spans: list[Any]) -> np.ndarray:
    """Continuous story hour per span: day*24 + period hour, interpolated inside each (day, period)."""
    groups: dict[tuple[Any, str], list[Any]] = {}
    for span in spans:
        day = _day_of(span)
        groups.setdefault((day, span.period), []).append(span)
    hour_by_id: dict[int, float] = {}
    for (day, period), group in groups.items():
        base = (day if day is not None else 1) * 24 + PERIOD_HOUR.get(period, 12)
        duration = PERIOD_DURATION.get(period, 4)
        ordered = sorted(group, key=lambda s: s.text_position)
        first = ordered[0].text_position
        last = ordered[-1].text_position
        for k, span in enumerate(ordered):
            frac = (span.text_position - first) / (last - first) if len(ordered) > 1 and last > first else 0.5
            hour_by_id[id(span)] = base + frac * duration
    return np.array([hour_by_id[id(span)] for span in spans], dtype=float)
