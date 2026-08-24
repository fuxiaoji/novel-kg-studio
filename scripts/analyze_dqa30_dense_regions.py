"""Offline gold-node density audit and publication figures for frozen DQA30 graphs.

Gold annotations are loaded only after the graph is frozen.  They never affect
retrieval, graph construction, layout optimization, or answer selection.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle  # noqa: E402
import networkx as nx  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_c_next10_graphs import merged_cases  # noqa: E402

DATA = ROOT.parent / "datasets" / "external" / "detectiveqa"
PARA_RE = re.compile(r"(?m)^\[(\d+)\]\s*")
SPACE_RE = re.compile(r"\s+")
LAYOUT_SEEDS = [20260824, 20260825, 20260826, 20260827, 20260828]
RELATION_COLORS = {
    "supports": "#2a9d8f",
    "contradicts": "#d1495b",
    "motive": "#e76f51",
    "means": "#f4a261",
    "opportunity": "#e9c46a",
    "before": "#457b9d",
    "after": "#457b9d",
    "causes": "#8f5da2",
}


def normalize(text: str) -> str:
    return SPACE_RE.sub(" ", str(text or "")).strip().lower()


def sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def annotation(novel: str, qid: str) -> dict[str, Any]:
    source = "human_anno" if "_human_anno_" in qid else "AIsup_anno"
    qi = int(qid.rsplit("_", 1)[-1])
    payload = json.loads((DATA / "anno_data_en" / source / f"{novel}.json").read_text(encoding="utf-8"))
    record = payload[0] if isinstance(payload, list) else payload
    return record["questions"][qi]


def paragraphs(text: str) -> dict[int, str]:
    matches = list(PARA_RE.finditer(text))
    result = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result[int(match.group(1))] = text[match.end():end].strip()
    return result


def gold_positions(novel: str, case: dict[str, Any]) -> tuple[set[int], set[int]]:
    clues: set[int] = set()
    answers: set[int] = set()
    for question in case["questions"]:
        row = annotation(novel, question["qid"])
        clues.update(int(value) for value in (row.get("clue_position") or []) if int(value) >= 0)
        answer = int(row.get("answer_position") or -1)
        if answer >= 0:
            answers.add(answer)
    return clues, answers


def evidence_values(node: dict[str, Any]) -> list[str]:
    value = node.get("evidence") or []
    if isinstance(value, str):
        value = [value]
    return [normalize(item) for item in value if len(normalize(item)) >= 8]


def overlap_positions(evidence: Iterable[str], normalized_paragraphs: dict[int, str], candidates: set[int]) -> set[int]:
    hits = set()
    for position in candidates:
        paragraph = normalized_paragraphs.get(position, "")
        if not paragraph:
            continue
        for item in evidence:
            if item in paragraph or (len(paragraph) <= len(item) and paragraph in item):
                hits.add(position)
                break
    return hits


def simple_graph(graph: dict[str, Any]) -> nx.Graph:
    result = nx.Graph()
    for node in graph.get("nodes") or []:
        result.add_node(str(node.get("id")))
    for edge in graph.get("edges") or []:
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source in result and target in result and source != target:
            result.add_edge(source, target)
    return result


def normalized_layout(graph: nx.Graph, seed: int) -> tuple[dict[str, np.ndarray], np.ndarray, float]:
    if not graph:
        return {}, np.zeros(2), 1.0
    positions = nx.spring_layout(
        graph,
        seed=seed,
        k=max(1.0 / math.sqrt(max(graph.number_of_nodes(), 1)), 0.035),
        iterations=80,
        weight=None,
        scale=1.0,
    )
    points = np.asarray(list(positions.values()), dtype=float)
    center = points.mean(axis=0)
    max_radius = max(float(np.linalg.norm(point - center)) for point in points) or 1.0
    return positions, center, max_radius


def visual_dense(graph: nx.Graph, positions: dict[str, np.ndarray], center: np.ndarray, max_radius: float) -> set[str]:
    return {
        node
        for node in graph
        if graph.degree(node) >= 2 and float(np.linalg.norm(positions[node] - center)) / max_radius <= 0.45
    }


def odds_ratio(a: int, b: int, c: int, d: int) -> float:
    return ((a + 0.5) * (d + 0.5)) / ((b + 0.5) * (c + 0.5))


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def density_summary(rows: list[dict[str, Any]], dense_key: str) -> dict[str, Any]:
    gold_total = sum(row["gold_nodes"] for row in rows)
    gold_dense = sum(row[f"gold_{dense_key}"] for row in rows)
    nongold_total = sum(row["nodes"] - row["gold_nodes"] for row in rows)
    nongold_dense = sum(row[dense_key] - row[f"gold_{dense_key}"] for row in rows)
    gold_rate = rate(gold_dense, gold_total)
    nongold_rate = rate(nongold_dense, nongold_total)
    enrichment = gold_rate / nongold_rate if gold_rate is not None and nongold_rate else None
    macro_values = [row[f"gold_{dense_key}"] / row["gold_nodes"] for row in rows if row["gold_nodes"]]
    return {
        "gold_dense": gold_dense,
        "gold_total": gold_total,
        "gold_rate_micro": gold_rate,
        "gold_rate_macro": sum(macro_values) / len(macro_values) if macro_values else None,
        "nongold_dense": nongold_dense,
        "nongold_total": nongold_total,
        "nongold_rate_micro": nongold_rate,
        "enrichment_ratio": enrichment,
        "odds_ratio_haldane": odds_ratio(gold_dense, gold_total - gold_dense, nongold_dense, nongold_total - nongold_dense),
    }


def cluster_bootstrap(rows: list[dict[str, Any]], dense_key: str, samples: int = 5000) -> dict[str, list[float]]:
    rng = random.Random(20260824)
    values: dict[str, list[float]] = defaultdict(list)
    for _ in range(samples):
        sample = [rng.choice(rows) for _ in rows]
        summary = density_summary(sample, dense_key)
        for key in ("gold_rate_micro", "enrichment_ratio", "odds_ratio_haldane"):
            value = summary.get(key)
            if value is not None and math.isfinite(value):
                values[key].append(float(value))
    result = {}
    for key, data in values.items():
        data.sort()
        result[key] = [data[int(0.025 * len(data))], data[min(int(0.975 * len(data)), len(data) - 1)]]
    return result


def plot_force_103(
    record: dict[str, Any],
    graph_payload: dict[str, Any],
    graph: nx.Graph,
    positions: dict[str, np.ndarray],
    center: np.ndarray,
    max_radius: float,
    gold_ids: set[str],
    answer_ids: set[str],
    out_dir: Path,
) -> None:
    by_id = {str(node.get("id")): node for node in graph_payload.get("nodes") or []}
    fig, ax = plt.subplots(figsize=(9.2, 8.2), constrained_layout=True)
    ax.set_facecolor("#fbfcfe")
    edge_groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in graph_payload.get("edges") or []:
        source, target = str(edge.get("source")), str(edge.get("target"))
        if source in positions and target in positions and source != target:
            edge_groups[str(edge.get("type") or "other")].append((source, target))
    for relation, edges in edge_groups.items():
        nx.draw_networkx_edges(
            graph,
            positions,
            edgelist=edges,
            edge_color=RELATION_COLORS.get(relation, "#aab4c3"),
            width=0.55 if relation not in RELATION_COLORS else 0.9,
            alpha=0.13 if relation not in RELATION_COLORS else 0.28,
            ax=ax,
        )
    ordinary = [node for node in graph if node not in gold_ids]
    clue_only = [node for node in gold_ids if node not in answer_ids]
    answer = list(answer_ids)
    nx.draw_networkx_nodes(graph, positions, nodelist=ordinary, node_size=13, node_color="#9fb6cf", alpha=0.56, linewidths=0, ax=ax)
    nx.draw_networkx_nodes(graph, positions, nodelist=clue_only, node_size=36, node_color="#f4a261", alpha=0.92, edgecolors="white", linewidths=0.45, ax=ax)
    nx.draw_networkx_nodes(graph, positions, nodelist=answer, node_size=72, node_color="#c43c4e", alpha=0.98, edgecolors="#5f1020", linewidths=1.0, ax=ax)
    label_candidates = sorted(gold_ids, key=lambda node: graph.degree(node), reverse=True)
    label_candidates = list(dict.fromkeys(list(answer_ids) + label_candidates))[:10]
    labels = {node: str(by_id.get(node, {}).get("name") or node)[:28] for node in label_candidates}
    nx.draw_networkx_labels(graph, positions, labels=labels, font_size=7.5, font_color="#182333", ax=ax)
    ax.add_patch(Circle(tuple(center), 0.45 * max_radius, facecolor="#f4a261", edgecolor="#bf6b2c", alpha=0.055, linewidth=1.0, linestyle="--"))
    ax.set_title("Frozen Novel 103 Knowledge Graph", fontsize=15, fontweight="bold", loc="left")
    ax.text(
        0.0,
        -0.035,
        f"n={graph.number_of_nodes()} nodes, m={graph.number_of_edges()} simple edges | orange: clue evidence | red: answer evidence",
        transform=ax.transAxes,
        fontsize=9,
        color="#4f5b6b",
    )
    ax.text(0.0, -0.065, "Force-layout distance is topological and does not encode narrative time or semantic distance.", transform=ax.transAxes, fontsize=8.2, color="#6f7782")
    ax.axis("off")
    for suffix in ("png", "pdf", "svg"):
        fig.savefig(out_dir / f"force_graph_novel103.{suffix}", dpi=320 if suffix == "png" else None, bbox_inches="tight")
    plt.close(fig)


def plot_enrichment(rows: list[dict[str, Any]], out_dir: Path) -> None:
    cohorts = [("Old 20", [row for row in rows if row["cohort"] == "old20"]), ("New 10", [row for row in rows if row["cohort"] == "new10"]), ("All 30\n(descriptive)", rows)]
    gold = [density_summary(group, "core2")["gold_rate_micro"] for _, group in cohorts]
    nongold = [density_summary(group, "core2")["nongold_rate_micro"] for _, group in cohorts]
    x = np.arange(len(cohorts))
    width = 0.34
    fig, ax = plt.subplots(figsize=(7.3, 4.4), constrained_layout=True)
    ax.bar(x - width / 2, gold, width, label="Gold-overlap nodes", color="#e76f51")
    ax.bar(x + width / 2, nongold, width, label="Other nodes", color="#8da9c4")
    ax.set_xticks(x, [name for name, _ in cohorts])
    ax.set_ylim(0, 1.04)
    ax.set_ylabel("Proportion in graph 2-core")
    ax.set_title("Gold-evidence nodes are concentrated in topological cores", loc="left", fontweight="bold")
    ax.grid(axis="y", color="#dde3ea", linewidth=0.7, alpha=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, ncol=2, loc="upper center")
    for positions_x, values in ((x - width / 2, gold), (x + width / 2, nongold)):
        for px, value in zip(positions_x, values):
            if value is not None:
                ax.text(px, value + 0.025, f"{value:.1%}", ha="center", va="bottom", fontsize=8.5)
    for suffix in ("png", "pdf", "svg"):
        fig.savefig(out_dir / f"gold_dense_core_enrichment.{suffix}", dpi=320 if suffix == "png" else None, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "config" / "dqa30_frozen_graphs.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "paper" / "generated")
    parser.add_argument("--layout-seeds", nargs="*", type=int, default=LAYOUT_SEEDS)
    args = parser.parse_args()
    frozen = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = frozen["records"]
    cases = merged_cases([row["novel"] for row in records])
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    node_rows = []
    figure_payload = None
    for record in records:
        novel = record["novel"]
        path = Path(record["graph_path"])
        if sha256(path) != record["sha256"]:
            raise RuntimeError(f"frozen graph hash mismatch: {novel}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        graph = simple_graph(payload)
        case = cases[novel]
        para = paragraphs(case["text"])
        normalized_paragraphs = {key: normalize(value) for key, value in para.items()}
        clue_positions, answer_positions = gold_positions(novel, case)
        all_positions = clue_positions | answer_positions
        gold_ids, answer_ids = set(), set()
        node_hits: dict[str, tuple[set[int], set[int]]] = {}
        for node in payload.get("nodes") or []:
            node_id = str(node.get("id"))
            values = evidence_values(node)
            clue_hits = overlap_positions(values, normalized_paragraphs, all_positions)
            answer_hits = overlap_positions(values, normalized_paragraphs, answer_positions)
            if clue_hits:
                gold_ids.add(node_id)
            if answer_hits:
                answer_ids.add(node_id)
            node_hits[node_id] = (clue_hits, answer_hits)
        core2 = set(nx.k_core(graph, k=2).nodes()) if graph.number_of_nodes() else set()
        layouts = []
        layout103 = None
        for seed in args.layout_seeds:
            positions, center, max_radius = normalized_layout(graph, seed)
            dense = visual_dense(graph, positions, center, max_radius)
            layouts.append(dense)
            if novel == "103" and seed == args.layout_seeds[0]:
                layout103 = (positions, center, max_radius, dense)
        primary_visual = layouts[0]
        covered_positions = set().union(*(hits[0] for hits in node_hits.values())) if node_hits else set()
        covered_answers = set().union(*(hits[1] for hits in node_hits.values())) if node_hits else set()
        row = {
            "novel": novel,
            "cohort": record["cohort"],
            "graph_builder": record["graph_builder"],
            "graph_sha256": record["sha256"],
            "nodes": graph.number_of_nodes(),
            "simple_edges": graph.number_of_edges(),
            "gold_positions": len(all_positions),
            "gold_positions_covered": len(covered_positions),
            "answer_positions": len(answer_positions),
            "answer_positions_covered": len(covered_answers),
            "gold_nodes": len(gold_ids),
            "answer_nodes": len(answer_ids),
            "core2": len(core2),
            "gold_core2": len(gold_ids & core2),
            "answer_core2": len(answer_ids & core2),
            "visual_dense": len(primary_visual),
            "gold_visual_dense": len(gold_ids & primary_visual),
            "answer_visual_dense": len(answer_ids & primary_visual),
            "visual_gold_rate_by_seed": [rate(len(gold_ids & dense), len(gold_ids)) for dense in layouts],
        }
        rows.append(row)
        for node in payload.get("nodes") or []:
            node_id = str(node.get("id"))
            clue_hits, answer_hits = node_hits[node_id]
            node_rows.append(
                {
                    "novel": novel,
                    "cohort": record["cohort"],
                    "node_id": node_id,
                    "name": node.get("name"),
                    "type": node.get("type"),
                    "degree": graph.degree(node_id),
                    "gold_overlap": node_id in gold_ids,
                    "answer_overlap": node_id in answer_ids,
                    "gold_positions": ";".join(map(str, sorted(clue_hits))),
                    "answer_positions": ";".join(map(str, sorted(answer_hits))),
                    "core2": node_id in core2,
                    "visual_dense": node_id in primary_visual,
                }
            )
        if novel == "103":
            if layout103 is None:
                raise RuntimeError("novel 103 layout missing")
            figure_payload = (record, payload, graph, *layout103[:3], gold_ids, answer_ids)
        print(f"audited dense regions: {novel}", flush=True)
    summaries = {}
    for cohort, subset in (("old20", [row for row in rows if row["cohort"] == "old20"]), ("new10", [row for row in rows if row["cohort"] == "new10"]), ("descriptive30", rows)):
        summaries[cohort] = {}
        for key in ("core2", "visual_dense"):
            summaries[cohort][key] = density_summary(subset, key)
            summaries[cohort][key]["novel_cluster_bootstrap_95"] = cluster_bootstrap(subset, key)
        summaries[cohort]["gold_position_recall"] = rate(sum(row["gold_positions_covered"] for row in subset), sum(row["gold_positions"] for row in subset))
        summaries[cohort]["answer_position_recall"] = rate(sum(row["answer_positions_covered"] for row in subset), sum(row["answer_positions"] for row in subset))
    report = {
        "metadata": {
            "protocol": "dqa30-gold-dense-audit-v1",
            "guard": "Gold was loaded after graph freeze and used only for offline scoring and visualization.",
            "visual_dense_definition": "undirected degree >= 2 and normalized spring-layout radius <= 0.45",
            "topological_dense_definition": "membership in the undirected simple graph 2-core",
            "layout_seeds": args.layout_seeds,
            "evidence_match": "normalized node evidence substring overlap; minimum 8 characters",
            "cohort_warning": frozen["cohort_warning"],
        },
        "summary": summaries,
        "per_novel": rows,
    }
    (args.out_dir / "dqa30_gold_dense_regions.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (args.out_dir / "dqa30_gold_dense_regions.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [key for key in rows[0] if key != "visual_gold_rate_by_seed"] + ["visual_gold_rate_by_seed"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "visual_gold_rate_by_seed": json.dumps(row["visual_gold_rate_by_seed"])})
    with (args.out_dir / "dqa30_gold_node_audit.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(node_rows[0]))
        writer.writeheader()
        writer.writerows(node_rows)
    if figure_payload is None:
        raise RuntimeError("novel 103 is absent from frozen manifest")
    plot_force_103(*figure_payload, args.out_dir)
    plot_enrichment(rows, args.out_dir)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
