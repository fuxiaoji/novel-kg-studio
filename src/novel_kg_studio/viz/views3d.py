from __future__ import annotations

from typing import Any

import networkx as nx
import numpy as np
import plotly.graph_objects as go

from ..schema import TYPE_COLORS, TYPE_LAYER


def compute_force_layout(nodes: list[dict[str, Any]], edges: list[dict[str, Any]], seed: int = 7) -> dict[str, np.ndarray]:
    graph = nx.Graph()
    for node in nodes:
        graph.add_node(node["id"])
    for edge in edges:
        graph.add_edge(edge["source"], edge["target"])
    if graph.number_of_nodes() == 0:
        return {}
    pos = nx.spring_layout(graph, dim=3, seed=seed, iterations=60)
    return {node_id: np.asarray(coords, dtype=float) for node_id, coords in pos.items()}


def _node_hover(node: dict[str, Any]) -> str:
    evidence = " | ".join(node.get("evidence", [])[:2])[:160]
    description = node.get("description") or ""
    attributes = node.get("attributes") or {}
    extra = f"<br>{description}" if description else ""
    if attributes:
        extra += "<br>" + ", ".join(f"{k}={v}" for k, v in list(attributes.items())[:3])
    return (
        f"{node['name']} ({node['type']})<br>day={node.get('day')} "
        f"text_pos={node.get('text_pos', 0):.3f} time_pos={node.get('time_pos', 0):.3f} "
        f"degree={node.get('degree', 0)} salience={node.get('salience', '-')}{extra}<br>{evidence}"
    )


def _build_3d_view(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    coords: dict[str, Any],
    *,
    title: str,
    axis_titles: tuple[str, str, str],
    edge_cap: int = 1500,
    gold_ids: set[str] | None = None,
) -> tuple[go.Figure, dict[str, Any]]:
    by_id = {n["id"]: n for n in nodes}
    traces: list[go.Trace] = []
    node_info: list[dict[str, Any]] = []
    for etype in TYPE_LAYER:
        subset = [n for n in nodes if n["type"] == etype]
        if not subset:
            continue
        traces.append(
            go.Scatter3d(
                x=[float(coords[n["id"]][0]) for n in subset],
                y=[float(coords[n["id"]][1]) for n in subset],
                z=[float(coords[n["id"]][2]) for n in subset],
                mode="markers",
                marker=dict(size=6, color=TYPE_COLORS[etype], opacity=1.0),
                text=[_node_hover(n) for n in subset],
                hoverinfo="text",
                name=etype,
            )
        )
        node_info.append({"idx": len(traces) - 1, "times": [float(n.get("time_pos", 0.5)) for n in subset]})
    if gold_ids:
        gold_nodes = [n for n in nodes if n["id"] in gold_ids]
        if gold_nodes:
            traces.append(
                go.Scatter3d(
                    x=[float(coords[n["id"]][0]) for n in gold_nodes],
                    y=[float(coords[n["id"]][1]) for n in gold_nodes],
                    z=[float(coords[n["id"]][2]) for n in gold_nodes],
                    mode="markers",
                    marker=dict(symbol="diamond", size=13, color="#ffd700", line=dict(color="#111111", width=1)),
                    text=[_node_hover(n) for n in gold_nodes],
                    hoverinfo="text",
                    name="金标线索（官方推理）",
                )
            )
    edges_sorted = sorted(edges, key=lambda e: -float(e.get("confidence") or 0))[:edge_cap]
    edge_info: list[dict[str, Any]] = []
    for edge in edges_sorted:
        source = by_id.get(edge["source"])
        target = by_id.get(edge["target"])
        if source is None or target is None:
            continue
        sc = coords[source["id"]]
        tc = coords[target["id"]]
        traces.append(
            go.Scatter3d(
                x=[float(sc[0]), float(tc[0]), None],
                y=[float(sc[1]), float(tc[1]), None],
                z=[float(sc[2]), float(tc[2]), None],
                mode="lines",
                line=dict(color="#8a8a8a", width=1.5),
                hoverinfo="skip",
                showlegend=False,
            )
        )
        edge_info.append(
            {
                "idx": len(traces) - 1,
                "t": max(float(source.get("time_pos", 0.5)), float(target.get("time_pos", 0.5))),
            }
        )
    fig = go.Figure(data=traces)
    fig.update_layout(
        title=title,
        height=620,
        scene=dict(
            xaxis_title=axis_titles[0],
            yaxis_title=axis_titles[1],
            zaxis_title=axis_titles[2],
        ),
        legend=dict(orientation="h", y=1.02),
    )
    return fig, {"node_traces": node_info, "edge_traces": edge_info}


def view_text_time_type(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    edge_cap: int = 1500,
    gold_ids: set[str] | None = None,
) -> tuple[go.Figure, dict[str, Any]]:
    coords = {
        n["id"]: (
            float(n.get("text_pos", 0.5)),
            float(n.get("time_pos", 0.5)),
            float(TYPE_LAYER.get(n["type"], 0)),
        )
        for n in nodes
    }
    return _build_3d_view(
        nodes,
        edges,
        coords,
        title="3D 视图 A：文本 × 时间 × 类型分层（滑块按小说时间播放）",
        axis_titles=("文本位置", "小说时间", "类型层"),
        edge_cap=edge_cap,
        gold_ids=gold_ids,
    )


def view_force(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    layout: dict[str, np.ndarray],
    *,
    edge_cap: int = 1500,
    gold_ids: set[str] | None = None,
) -> tuple[go.Figure, dict[str, Any]]:
    return _build_3d_view(
        nodes,
        edges,
        layout,
        title="3D 视图 B：力导向布局（颜色=类型，滑块按时间窗口显示）",
        axis_titles=("fx", "fy", "fz"),
        edge_cap=edge_cap,
        gold_ids=gold_ids,
    )


def view_density(nodes: list[dict[str, Any]], grid: int = 40) -> go.Figure:
    xs = np.array([float(n.get("text_pos", 0.5)) for n in nodes], dtype=float)
    ys = np.array([float(n.get("time_pos", 0.5)) for n in nodes], dtype=float)
    hist, xedges, yedges = np.histogram2d(xs, ys, bins=grid, range=[[0.0, 1.0], [0.0, 1.0]])
    fig = go.Figure(go.Surface(z=hist.T, x=xedges, y=yedges, colorscale="YlOrRd", colorbar=dict(title="节点数")))
    fig.update_layout(
        title="3D 视图 C：文本 × 时间稠密度场",
        height=620,
        scene=dict(xaxis_title="文本位置", yaxis_title="小说时间", zaxis_title="节点数"),
    )
    return fig
