from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .bm25 import BM25Index


@dataclass
class RetrievalResult:
    question: str
    first_order: list[str] = field(default_factory=list)
    second_order: list[str] = field(default_factory=list)
    third_order: list[str] = field(default_factory=list)
    chains: list[dict[str, Any]] = field(default_factory=list)
    subgraph: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "first_order": self.first_order,
            "second_order": self.second_order,
            "third_order": self.third_order,
            "chains": self.chains,
            "subgraph": self.subgraph,
        }


class GraphStore:
    """Knowledge-graph-backed RAG: BM25 over node text + first/second-order graph expansion."""

    def __init__(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        self.nodes = nodes
        self.edges = edges
        self.by_id = {n["id"]: n for n in nodes}
        self.adj: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for edge in edges:
            self.adj.setdefault(edge["source"], []).append((edge["target"], edge))
            self.adj.setdefault(edge["target"], []).append((edge["source"], edge))
        self.index = BM25Index([self._doc(n) for n in nodes])

    def _doc(self, node: dict[str, Any]) -> str:
        """Node text + one-hop graph context (neighbor names, edge types/evidence)."""
        parts = [
            node.get("name", ""),
            *node.get("aliases", []),
            node.get("description", ""),
            *node.get("evidence", []),
        ]
        attributes = node.get("attributes") or {}
        parts.extend(f"{key}:{value}" for key, value in list(attributes.items())[:4])
        for neighbor, edge in self.adj.get(node["id"], [])[:8]:
            parts.append(self.by_id.get(neighbor, {}).get("name", ""))
            parts.append(str(edge.get("type") or ""))
            parts.append(str(edge.get("evidence") or ""))
        return " ".join(parts)

    def retrieve(self, question: str, *, k1: int = 8, k2: int = 12, k3: int = 0) -> RetrievalResult:
        scores = self.index.score(question)
        order = np.argsort(-scores)
        first_order: list[str] = []
        for idx in order:
            if scores[idx] <= 0:
                break
            first_order.append(self.nodes[idx]["id"])
            if len(first_order) >= k1:
                break
        first_set = set(first_order)
        second_scores: dict[str, float] = {}
        for node_id in first_order:
            for neighbor, edge in self.adj.get(node_id, []):
                if neighbor in first_set:
                    continue
                second_scores[neighbor] = max(second_scores.get(neighbor, 0.0), float(edge.get("confidence") or 0.0))
        second_order = sorted(second_scores, key=lambda nid: (-second_scores[nid], nid))[:k2]
        third_order: list[str] = []
        if k3 > 0:
            second_set = set(second_order)
            third_scores: dict[str, float] = {}
            for node_id in second_order:
                for neighbor, edge in self.adj.get(node_id, []):
                    if neighbor in first_set or neighbor in second_set or neighbor in third_scores:
                        continue
                    third_scores[neighbor] = max(third_scores.get(neighbor, 0.0), float(edge.get("confidence") or 0.0))
            third_order = sorted(third_scores, key=lambda nid: (-third_scores[nid], nid))[:k3]
        chains = self._chains(first_order, second_order)
        subgraph = self._subgraph(list(first_set) + second_order + third_order)
        return RetrievalResult(
            question=question,
            first_order=first_order,
            second_order=second_order,
            third_order=third_order,
            chains=chains,
            subgraph=subgraph,
        )

    def _chains(self, first_order: list[str], second_order: list[str]) -> list[dict[str, Any]]:
        chains: list[dict[str, Any]] = []
        second_set = set(second_order)
        for node_id in first_order:
            node = self.by_id[node_id]
            linked = []
            for neighbor, edge in sorted(self.adj.get(node_id, []), key=lambda pair: -float(pair[1].get("confidence") or 0.0)):
                if neighbor in second_set or neighbor == node_id:
                    linked.append(
                        {
                            "name": self.by_id[neighbor]["name"],
                            "type": self.by_id[neighbor]["type"],
                            "edge_type": edge["type"],
                            "evidence": edge["evidence"],
                            "confidence": edge.get("confidence"),
                        }
                    )
                if len(linked) >= 3:
                    break
            chains.append(
                {
                    "clue": node["name"],
                    "type": node["type"],
                    "evidence": node.get("evidence", []),
                    "text_pos": node.get("text_pos"),
                    "time_pos": node.get("time_pos"),
                    "linked": linked,
                }
            )
        return chains

    def _subgraph(self, node_ids: list[str]) -> dict[str, Any]:
        node_set = set(node_ids)
        sub_edges = [e for e in self.edges if e["source"] in node_set and e["target"] in node_set]
        return {
            "nodes": [self.by_id[nid] for nid in node_ids if nid in self.by_id],
            "edges": sub_edges,
        }
