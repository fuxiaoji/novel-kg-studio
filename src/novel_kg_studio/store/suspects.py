"""Suspect-chain retrieval: aggregate motive/means/opportunity edges for who-questions."""

from __future__ import annotations

import re
from typing import Any

SUSPECT_EDGE_TYPES = ("motive", "means", "opportunity", "contradicts", "supports")
WHO_PATTERN = re.compile(r"\b(who|killer|murderer|murder|thief|stole|stolen|robber|burglar)\b", re.IGNORECASE)


def is_who_question(question: str) -> bool:
    return bool(WHO_PATTERN.search(str(question or "")))


def suspect_chain(store: Any, question: str, cap: int = 4, victim_name: str = "") -> list[dict[str, Any]]:
    """Rank persons by their motive/means/opportunity/contradiction edges (decoy-downweighted)."""
    scores: dict[str, float] = {}
    edge_by_person: dict[str, list[dict[str, Any]]] = {}
    for edge in store.edges:
        if edge["type"] not in SUSPECT_EDGE_TYPES:
            continue
        weight = float(edge.get("confidence") or 0.0)
        if edge.get("decoy"):
            weight *= 0.3
        weight *= max(int(edge.get("importance") or 3), 1) / 3.0
        node_id = edge["source"]
        node = store.by_id.get(node_id)
        if node is None or node["type"] != "person":
            continue
        if victim_name and node["name"].lower() == str(victim_name).lower():
            continue
        scores[node_id] = scores.get(node_id, 0.0) + weight
        edge_by_person.setdefault(node_id, []).append(edge)
    ranked = sorted(scores, key=lambda nid: -scores[nid])[:cap]
    rows = []
    for node_id in ranked:
        node = store.by_id[node_id]
        edges = sorted(edge_by_person[node_id], key=lambda e: -float(e.get("confidence") or 0.0))[:4]
        rows.append(
            {
                "name": node["name"],
                "score": round(scores[node_id], 3),
                "edges": [
                    {
                        "type": e["type"],
                        "evidence": str(e.get("evidence") or "")[:120],
                        "confidence": float(e.get("confidence") or 0.0),
                        "decoy": bool(e.get("decoy", False)),
                    }
                    for e in edges
                ],
            }
        )
    return rows


def format_suspect_chain(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    lines = ["Suspect chains (ranked by motive/means/opportunity evidence):"]
    for row in rows:
        lines.append(f"- {row['name']} (score={row['score']})")
        for edge in row["edges"]:
            tag = " [DECOY]" if edge["decoy"] else ""
            lines.append(f"    {edge['type']}{tag}: {edge['evidence']}")
        for sentence in row.get("source_sentences", []):
            lines.append(f"    source: {sentence[:130]}")
    return "\n".join(lines)
