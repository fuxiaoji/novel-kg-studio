"""Render publication figures from frozen DQA30 artifacts only.

This module never calls an LLM and never writes graph files.  Gold labels are
read only from the post-hoc node audit produced by analyze_dqa30_dense_regions.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RELATION_COLORS = {
    "appears_in": "#7895b2", "participates_in": "#7895b2",
    "before": "#75a478", "after": "#75a478", "causes": "#8b6bb1",
    "supports": "#d08b46", "contradicts": "#c95b65", "related_to": "#9aa6b2",
}


def load_audit(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["node_id"]: row for row in csv.DictReader(handle) if row["novel"] == "103"}


def simple_graph(payload: dict) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(str(node["id"]) for node in payload.get("nodes", []))
    for edge in payload.get("edges", []):
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source != target and source in graph and target in graph:
            graph.add_edge(source, target)
    return graph


def force_figure(manifest: Path, audit_path: Path, out_dir: Path) -> None:
    frozen = json.loads(manifest.read_text(encoding="utf-8"))
    record = next(item for item in frozen["novels"] if str(item["novel"]) == "103")
    graph_path = ROOT / record["graph_path"]
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    full = simple_graph(payload)
    component = max(nx.connected_components(full), key=len)
    graph = full.subgraph(component).copy()
    pos = nx.spring_layout(graph, seed=20260824, k=1.25 / math.sqrt(graph.number_of_nodes()), iterations=450)
    audit = load_audit(audit_path)
    by_id = {str(node["id"]): node for node in payload.get("nodes", [])}
    gold = {node for node in graph if audit.get(node, {}).get("gold_overlap", "").lower() == "true"}
    answer = {node for node in graph if audit.get(node, {}).get("answer_overlap", "").lower() == "true"}

    fig, ax = plt.subplots(figsize=(9.3, 7.6), constrained_layout=True)
    ax.set_facecolor("#fbfcfe")
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in payload.get("edges", []):
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source in graph and target in graph and source != target:
            grouped[str(edge.get("type") or "related_to")].append((source, target))
    for relation, edges in grouped.items():
        nx.draw_networkx_edges(graph, pos, edgelist=edges, edge_color=RELATION_COLORS.get(relation, "#aab4c3"), width=0.75, alpha=0.18, ax=ax)
    ordinary = [node for node in graph if node not in gold]
    clue_only = [node for node in gold if node not in answer]
    nx.draw_networkx_nodes(graph, pos, nodelist=ordinary, node_size=18, node_color="#9fb6cf", alpha=0.64, linewidths=0, ax=ax)
    nx.draw_networkx_nodes(graph, pos, nodelist=clue_only, node_size=48, node_color="#f4a261", alpha=0.96, edgecolors="white", linewidths=0.55, ax=ax)
    nx.draw_networkx_nodes(graph, pos, nodelist=list(answer), node_size=86, node_color="#c43c4e", alpha=0.99, edgecolors="#5f1020", linewidths=1.15, ax=ax)
    candidates = list(dict.fromkeys(list(answer) + sorted(gold, key=graph.degree, reverse=True)))[:7]
    for index, node in enumerate(candidates):
        label = str(by_id.get(node, {}).get("name") or node)[:26]
        ax.annotate(label, xy=pos[node], xytext=(7, 7 + 4 * (index % 3)), textcoords="offset points", fontsize=7.2, color="#182333", bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": "#cad3dd", "alpha": 0.9, "linewidth": 0.45}, arrowprops={"arrowstyle": "-", "color": "#98a5b2", "alpha": 0.65, "linewidth": 0.45})
    ax.set_title("Frozen Novel 103 Knowledge Graph", fontsize=15, fontweight="bold", loc="left")
    ax.text(0, -0.035, f"Largest component: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges; full graph: {full.number_of_nodes()} nodes, {full.number_of_edges()} edges", transform=ax.transAxes, fontsize=8.8, color="#4f5b6b")
    ax.text(0, -0.065, "Orange: clue-overlap node; dark red with outline: answer-overlap node. Layout distance is topological, not narrative or semantic distance.", transform=ax.transAxes, fontsize=8.1, color="#687483")
    ax.axis("off")
    for suffix in ("png", "pdf", "svg"):
        fig.savefig(out_dir / f"force_graph_novel103_paper.{suffix}", dpi=360 if suffix == "png" else None, bbox_inches="tight")
    plt.close(fig)


def accuracy_figure(results_path: Path, out_dir: Path) -> None:
    if not results_path.is_file():
        return
    report = json.loads(results_path.read_text(encoding="utf-8"))
    methods = ["G1", "G2", "G3", "G4", "G5", "B1", "B2", "B3", "Q0"]
    labels = ["G1", "G2", "G3", "G4", "G5", "Tail", "Compress", "RAG", "Q0"]
    all_values = [report["descriptive30"]["all"][method]["micro_accuracy"] for method in methods]
    hard_values = [report["descriptive30"]["q0_wrong"].get(method, {}).get("micro_accuracy", np.nan) for method in methods]
    x = np.arange(len(methods)); width = 0.36
    fig, ax = plt.subplots(figsize=(9.0, 4.6), constrained_layout=True)
    colors = ["#4f7cac"] * 5 + ["#9aa6b2"] * 3 + ["#d0a04b"]
    ax.bar(x - width / 2, all_values, width, color=colors, alpha=0.95, label="All questions")
    ax.bar(x + width / 2, hard_values, width, color=colors, alpha=0.42, hatch="//", label="Q0-hard")
    ax.set_xticks(x, labels); ax.set_ylim(0, 0.72); ax.set_ylabel("Accuracy")
    ax.set_title("Frozen 30-novel evaluation (descriptive pooled result)", loc="left", fontweight="bold")
    ax.grid(axis="y", color="#e0e5eb", linewidth=0.7); ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, ncol=2)
    for suffix in ("png", "pdf", "svg"):
        fig.savefig(out_dir / f"dqa30_accuracy.{suffix}", dpi=360 if suffix == "png" else None, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "config" / "dqa30_frozen_graphs.json")
    parser.add_argument("--audit", type=Path, default=ROOT / "paper" / "generated" / "dqa30_gold_node_audit.csv")
    parser.add_argument("--results", type=Path, default=ROOT / "paper" / "generated" / "dqa30_frozen_results.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "paper" / "generated")
    args = parser.parse_args(); args.out_dir.mkdir(parents=True, exist_ok=True)
    force_figure(args.manifest, args.audit, args.out_dir)
    accuracy_figure(args.results, args.out_dir)


if __name__ == "__main__":
    main()
