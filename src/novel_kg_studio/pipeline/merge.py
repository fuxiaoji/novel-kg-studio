from __future__ import annotations

from collections import Counter
import re
from typing import Any

from ..chunking import find_span
from ..schema import canonical_name, norm_text


class GraphMerger:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.alias_to_id: dict[str, str] = {}
        self.edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._node_seq = 0
        self._edge_seq = 0
        self.dropped_mentions = 0
        self.dropped_relations = 0
        self.deduplicated_relations = 0
        self.dropped_relation_endpoints = 0
        self.dropped_relation_sentence = 0
        self.dropped_relation_evidence = 0
        self.recovered_relation_evidence = 0
        self.pruned_ungrounded_isolates = 0

    def _new_node_id(self) -> str:
        self._node_seq += 1
        return f"n{self._node_seq}"

    def resolve(self, name: str) -> str | None:
        key = canonical_name(name)
        if not key:
            return None
        node_id = self.alias_to_id.get(key)
        if node_id is not None:
            return node_id
        return None

    def add_entity(
        self,
        *,
        name: str,
        etype: str,
        aliases: list[str],
        mentions: list[dict[str, Any]],
        mention_positions: list[float],
        mention_time_positions: list[float],
        mention_days: list[int | None],
        description: str = "",
        salience: int = 3,
        attributes: dict | None = None,
    ) -> str | None:
        key = canonical_name(name)
        if not key:
            return None
        node_id = self.resolve(name)
        if node_id is None:
            for alias in aliases:
                node_id = self.resolve(alias)
                if node_id is not None:
                    break
        if node_id is None:
            node_id = self._new_node_id()
            self.nodes[node_id] = {
                "id": node_id,
                "name": name,
                "type": etype,
                "aliases": [],
                "mentions": [],
                "evidence": [],
                "positions": [],
                "time_positions": [],
                "day_values": [],
                "mention_count": 0,
                "description": "",
                "salience": 0,
                "attributes": {},
            }
        node = self.nodes[node_id]
        for key in [key, *(canonical_name(a) for a in aliases)]:
            if key:
                self.alias_to_id[key] = node_id
        for alias in aliases:
            if alias and alias not in node["aliases"]:
                node["aliases"].append(alias)
        for m in mentions:
            text = str(m.get("text") or "").strip()
            if not text:
                continue
            node["mentions"].append(text)
            node["mention_count"] += 1
            if text not in node["evidence"]:
                node["evidence"].append(text)
        node["positions"].extend(mention_positions)
        node["time_positions"].extend(mention_time_positions)
        node["day_values"].extend(d for d in mention_days if d is not None)
        if len(str(description or "")) > len(node["description"]):
            node["description"] = str(description or "")
        node["salience"] = max(node["salience"], int(salience or 0))
        node["attributes"].update(dict(attributes or {}))
        node["evidence"] = node["evidence"][:3]
        return node_id

    def add_relation(
        self,
        *,
        source_id: str,
        target_id: str,
        rtype: str,
        evidence: str,
        confidence: float,
        day: int | None,
        decoy: bool = False,
        importance: int = 3,
    ) -> None:
        key = (source_id, target_id, rtype, norm_text(evidence))
        if key in self.edges:
            self.deduplicated_relations += 1
            return
        self._edge_seq += 1
        self.edges[key] = {
            "id": f"e{self._edge_seq}",
            "source": source_id,
            "target": target_id,
            "type": rtype,
            "evidence": evidence[:200],
            "confidence": confidence,
            "day": day,
            "decoy": decoy,
            "importance": importance,
        }

    def prune_ungrounded_isolates(self) -> None:
        connected = {
            node_id
            for edge in self.edges.values()
            for node_id in (edge["source"], edge["target"])
        }
        doomed = {
            node_id
            for node_id, node in self.nodes.items()
            if node_id not in connected and int(node.get("mention_count", 0) or 0) == 0
        }
        if not doomed:
            return
        self.pruned_ungrounded_isolates += len(doomed)
        for node_id in doomed:
            self.nodes.pop(node_id, None)
        self.alias_to_id = {
            alias: node_id for alias, node_id in self.alias_to_id.items() if node_id not in doomed
        }

    def finalize(self, novel_len: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        degree: Counter = Counter()
        for edge in self.edges.values():
            degree[edge["source"]] += 1
            degree[edge["target"]] += 1
        nodes: list[dict[str, Any]] = []
        for node_id, node in self.nodes.items():
            positions = node["positions"] or [0.0]
            time_positions = node["time_positions"] or [0.5]
            day_values = [d for d in node["day_values"] if d is not None]
            nodes.append(
                {
                    "id": node_id,
                    "name": node["name"],
                    "type": node["type"],
                    "aliases": node["aliases"],
                    "evidence": node["evidence"],
                    "text_pos": sum(positions) / len(positions) / max(novel_len, 1),
                    "time_pos": sum(time_positions) / len(time_positions),
                    "day": round(sum(day_values) / len(day_values)) if day_values else None,
                    "mention_count": node["mention_count"],
                    "degree": degree.get(node_id, 0),
                    "description": node["description"],
                    "salience": node["salience"],
                    "attributes": dict(node["attributes"]),
                }
            )
        edges = sorted(self.edges.values(), key=lambda e: (-e["confidence"], e["id"]))
        stats = {
            "num_nodes": len(nodes),
            "num_edges": len(edges),
            "node_types": dict(Counter(n["type"] for n in nodes)),
            "edge_types": dict(Counter(e["type"] for e in edges)),
            "dropped_mentions": self.dropped_mentions,
            "dropped_relations": self.dropped_relations,
            "deduplicated_relations": self.deduplicated_relations,
            "dropped_relation_endpoints": self.dropped_relation_endpoints,
            "dropped_relation_sentence": self.dropped_relation_sentence,
            "dropped_relation_evidence": self.dropped_relation_evidence,
            "recovered_relation_evidence": self.recovered_relation_evidence,
            "pruned_ungrounded_isolates": self.pruned_ungrounded_isolates,
        }
        return nodes, edges, stats


def _ground_relation_evidence(text: str, evidence: str) -> tuple[str | None, bool]:
    """Return grounded text; conservatively repair only ordered ellipsis abbreviations."""
    normalized_text = norm_text(text)
    normalized_evidence = norm_text(evidence)
    if not normalized_evidence:
        return None, False
    start, end = find_span(text, evidence)
    if start >= 0:
        return normalized_text[start:end], False

    pieces = [norm_text(part) for part in re.split(r"(?:\.\.\.|…)", evidence)]
    pieces = [part for part in pieces if part]
    if len(pieces) < 2 or any(len(part) < 8 for part in pieces):
        return None, False
    cursor = 0
    first = -1
    last = -1
    for part in pieces:
        pos = normalized_text.find(part, cursor)
        if pos < 0:
            return None, False
        if first < 0:
            first = pos
        last = pos + len(part)
        cursor = last
    if sum(len(part) for part in pieces) < 20:
        return None, False
    return normalized_text[first:last], True


def build_graph(
    pass2_records: list[dict[str, Any]],
    kept_by_seq: dict[int, Any],
    novel_len: int,
    log: Any = print,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    merger = GraphMerger()

    # Register every entity and alias before resolving relations. This permits a
    # relation to refer to a canonical entity introduced in a later Pass2 chunk.
    for record in pass2_records:
        for entity in record.get("entities") or []:
            mention_positions: list[float] = []
            mention_time_positions: list[float] = []
            mention_days: list[int | None] = []
            kept_mentions: list[dict[str, Any]] = []
            for mention in entity.get("mentions") or []:
                sentence_index = int(mention["sentence_index"])
                span = kept_by_seq.get(sentence_index)
                if span is None:
                    merger.dropped_mentions += 1
                    continue
                start, _ = find_span(span.text, mention["text"])
                if start < 0:
                    merger.dropped_mentions += 1
                    continue
                kept_mentions.append(mention)
                mention_positions.append(span.char_start + start)
                mention_time_positions.append(span.time_position)
                mention_days.append(span.day)
            merger.add_entity(
                name=entity["name"],
                etype=entity["type"],
                aliases=entity.get("aliases") or [],
                mentions=kept_mentions,
                mention_positions=mention_positions,
                mention_time_positions=mention_time_positions,
                mention_days=mention_days,
                description=entity.get("description") or "",
                salience=int(entity.get("salience") or 3),
                attributes=entity.get("attributes") or {},
            )

    for record in pass2_records:
        for relation in record.get("relations") or []:
            source_id = merger.resolve(relation["source"])
            target_id = merger.resolve(relation["target"])
            if source_id is None or target_id is None:
                merger.dropped_relations += 1
                merger.dropped_relation_endpoints += 1
                continue
            span = kept_by_seq.get(int(relation["sentence_index"]))
            if span is None:
                merger.dropped_relations += 1
                merger.dropped_relation_sentence += 1
                continue
            grounded, recovered = _ground_relation_evidence(span.text, relation["evidence"])
            if grounded is None:
                merger.dropped_relations += 1
                merger.dropped_relation_evidence += 1
                continue
            if recovered:
                merger.recovered_relation_evidence += 1
            merger.add_relation(
                source_id=source_id,
                target_id=target_id,
                rtype=relation["type"],
                evidence=grounded,
                confidence=float(relation.get("confidence") or 0.9),
                day=span.day,
                decoy=bool(relation.get("decoy", False)),
                importance=int(relation.get("importance") or 3),
            )

    merger.prune_ungrounded_isolates()
    log(
        f"[merge] dropped_mentions={merger.dropped_mentions} dropped_relations={merger.dropped_relations} "
        f"(endpoints={merger.dropped_relation_endpoints}, sentence={merger.dropped_relation_sentence}, "
        f"evidence={merger.dropped_relation_evidence}) recovered_evidence={merger.recovered_relation_evidence} "
        f"pruned_ungrounded_isolates={merger.pruned_ungrounded_isolates} "
        f"dedup_relations={merger.deduplicated_relations}"
    )
    return merger.finalize(novel_len)
