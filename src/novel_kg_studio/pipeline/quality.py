from __future__ import annotations

from typing import Any


def evaluate_graph_quality(
    graph: dict[str, Any],
    *,
    max_isolate_rate: float = 0.60,
    min_edge_node_ratio: float = 0.50,
    max_dropped_relation_rate: float = 0.55,
) -> dict[str, Any]:
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    node_ids = {str(node.get("id")) for node in nodes}
    connected: set[str] = set()
    dangling_edges = 0
    for edge in edges:
        source = str(edge.get("source"))
        target = str(edge.get("target"))
        if source not in node_ids or target not in node_ids:
            dangling_edges += 1
            continue
        connected.update((source, target))

    isolates = sum(str(node.get("id")) not in connected for node in nodes)
    ungrounded_nodes = sum(
        not (node.get("evidence") or node.get("source_sentence_ids"))
        for node in nodes
    )
    ungrounded_isolates = sum(
        str(node.get("id")) not in connected
        and not (node.get("evidence") or node.get("source_sentence_ids"))
        for node in nodes
    )
    node_count = len(nodes)
    edge_count = len(edges)
    isolate_rate = isolates / node_count if node_count else 1.0
    edge_node_ratio = edge_count / node_count if node_count else 0.0

    merge = dict(graph.get("merge_stats") or {})
    dropped = int(merge.get("dropped_relations") or 0)
    deduplicated = int(merge.get("deduplicated_relations") or 0)
    relation_candidates = edge_count + dropped + deduplicated
    dropped_relation_rate = dropped / relation_candidates if relation_candidates else 1.0

    failures: list[str] = []
    warnings: list[str] = []
    if not nodes:
        failures.append("graph has no nodes")
    if not edges:
        failures.append("graph has no edges")
    if isolate_rate > max_isolate_rate:
        failures.append(f"isolate_rate {isolate_rate:.3f} exceeds {max_isolate_rate:.3f}")
    if edge_node_ratio < min_edge_node_ratio:
        failures.append(
            f"edge_node_ratio {edge_node_ratio:.3f} is below {min_edge_node_ratio:.3f}"
        )
    if dropped_relation_rate > max_dropped_relation_rate:
        failures.append(
            f"dropped_relation_rate {dropped_relation_rate:.3f} exceeds "
            f"{max_dropped_relation_rate:.3f}"
        )
    if dangling_edges:
        failures.append(f"{dangling_edges} edges reference missing nodes")
    consolidation = dict(graph.get("consolidation_stats") or {})
    if (
        int(consolidation.get("candidate_persons") or 0) >= 80
        and int(consolidation.get("merged_nodes") or 0) == 0
    ):
        warnings.append("entity consolidation merged zero nodes despite >=80 candidate persons")
    if ungrounded_nodes:
        warnings.append(f"{ungrounded_nodes} nodes have no grounded evidence")
    if ungrounded_isolates:
        failures.append(f"{ungrounded_isolates} isolated nodes have no grounded evidence")

    return {
        "passed": not failures,
        "thresholds": {
            "max_isolate_rate": max_isolate_rate,
            "min_edge_node_ratio": min_edge_node_ratio,
            "max_dropped_relation_rate": max_dropped_relation_rate,
        },
        "metrics": {
            "nodes": node_count,
            "edges": edge_count,
            "isolates": isolates,
            "isolate_rate": isolate_rate,
            "edge_node_ratio": edge_node_ratio,
            "ungrounded_nodes": ungrounded_nodes,
            "ungrounded_isolates": ungrounded_isolates,
            "dangling_edges": dangling_edges,
            "dropped_relations": dropped,
            "relation_candidates": relation_candidates,
            "dropped_relation_rate": dropped_relation_rate,
        },
        "failures": failures,
        "warnings": warnings,
    }
