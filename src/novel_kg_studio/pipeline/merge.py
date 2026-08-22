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
        self.recovered_relation_sentence_index = 0
        self.recovered_mentions = 0
        self.skipped_unreferenced_entities = 0
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
            "recovered_relation_sentence_index": self.recovered_relation_sentence_index,
            "recovered_mentions": self.recovered_mentions,
            "skipped_unreferenced_entities": self.skipped_unreferenced_entities,
            "pruned_ungrounded_isolates": self.pruned_ungrounded_isolates,
        }
        return nodes, edges, stats


_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)


def _tokens_with_offsets(value: str) -> list[tuple[str, int, int]]:
    return [(match.group(0).lower(), match.start(), match.end()) for match in _TOKEN_RE.finditer(value)]


def _find_token_sequence(text: str, phrase: str, *, after: int = 0) -> tuple[int, int]:
    """Find the same lexical sequence while tolerating punctuation only."""
    text_tokens = _tokens_with_offsets(text)
    phrase_tokens = [token for token, _, _ in _tokens_with_offsets(phrase)]
    if not phrase_tokens:
        return (-1, -1)
    width = len(phrase_tokens)
    for start in range(len(text_tokens) - width + 1):
        if text_tokens[start][1] < after:
            continue
        if [token for token, _, _ in text_tokens[start : start + width]] == phrase_tokens:
            return (text_tokens[start][1], text_tokens[start + width - 1][2])
    return (-1, -1)


def _ground_relation_evidence(text: str, evidence: str) -> tuple[str | None, bool]:
    """Return an original-text substring; never accept semantic paraphrases."""
    evidence = str(evidence or "").strip()
    if not evidence:
        return None, False
    raw_start = text.find(evidence)
    if raw_start >= 0:
        return text[raw_start : raw_start + len(evidence)], False

    has_ellipsis = bool(re.search(r"(?:\.\.\.|…)", evidence))
    pieces = [part.strip() for part in re.split(r"(?:\.\.\.|…)", evidence) if part.strip()]
    if not pieces:
        return None, False
    lexical_lengths = [sum(len(token) for token, _, _ in _tokens_with_offsets(part)) for part in pieces]
    if sum(lexical_lengths) < (16 if has_ellipsis else 12):
        return None, False
    if len(pieces) > 1 and any(length < 8 for length in lexical_lengths):
        return None, False
    cursor = 0
    first = -1
    last = -1
    for part in pieces:
        start, end = _find_token_sequence(text, part, after=cursor)
        if start < 0:
            return None, False
        if first < 0:
            first = start
        last = end
        cursor = end
    return text[first:last], True


def _allowed_line_indices(record: dict[str, Any]) -> list[int]:
    result: list[int] = []
    for value in record.get("line_indices") or []:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(result))


def _relocate_mention(
    kept_by_seq: dict[int, Any], requested_index: int, text: str, allowed: list[int]
) -> tuple[Any | None, int]:
    span = kept_by_seq.get(requested_index)
    if span is not None and find_span(span.text, text)[0] >= 0:
        return span, requested_index
    matches = [
        (kept_by_seq[index], index)
        for index in allowed
        if index in kept_by_seq and find_span(kept_by_seq[index].text, text)[0] >= 0
    ]
    return matches[0] if len(matches) == 1 else (None, requested_index)


def _relocate_relation(
    kept_by_seq: dict[int, Any], requested_index: int, evidence: str, allowed: list[int]
) -> tuple[Any | None, str | None, bool, int]:
    span = kept_by_seq.get(requested_index)
    if span is not None:
        grounded, repaired = _ground_relation_evidence(span.text, evidence)
        if grounded is not None:
            return span, grounded, repaired, requested_index
    matches: list[tuple[Any, str, bool, int]] = []
    for index in allowed:
        candidate = kept_by_seq.get(index)
        if candidate is None:
            continue
        grounded, repaired = _ground_relation_evidence(candidate.text, evidence)
        if grounded is not None:
            matches.append((candidate, grounded, repaired, index))
    if len(matches) == 1:
        return matches[0]
    return None, None, False, requested_index


def build_graph(
    pass2_records: list[dict[str, Any]],
    kept_by_seq: dict[int, Any],
    novel_len: int,
    log: Any = print,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    merger = GraphMerger()

    referenced_entity_keys = {
        key
        for record in pass2_records
        for relation in (record.get("relations") or [])
        for key in (canonical_name(relation.get("source")), canonical_name(relation.get("target")))
        if key
    }

    # Register every entity and alias before resolving relations. This permits a
    # relation to refer to a canonical entity introduced in a later Pass2 chunk.
    for record in pass2_records:
        allowed_indices = _allowed_line_indices(record)
        for entity in record.get("entities") or []:
            entity_keys = {
                key
                for key in [canonical_name(entity.get("name")), *(canonical_name(alias) for alias in entity.get("aliases") or [])]
                if key
            }
            if entity_keys.isdisjoint(referenced_entity_keys):
                merger.skipped_unreferenced_entities += 1
                merger.pruned_ungrounded_isolates += 1
                continue
            mention_positions: list[float] = []
            mention_time_positions: list[float] = []
            mention_days: list[int | None] = []
            kept_mentions: list[dict[str, Any]] = []
            for mention in entity.get("mentions") or []:
                sentence_index = int(mention["sentence_index"])
                span, actual_index = _relocate_mention(
                    kept_by_seq, sentence_index, str(mention.get("text") or ""), allowed_indices
                )
                if span is None:
                    merger.dropped_mentions += 1
                    continue
                start, _ = find_span(span.text, mention["text"])
                if start < 0:
                    merger.dropped_mentions += 1
                    continue
                kept_mentions.append({**mention, "sentence_index": actual_index})
                if actual_index != sentence_index:
                    merger.recovered_mentions += 1
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
        allowed_indices = _allowed_line_indices(record)
        for relation in record.get("relations") or []:
            source_id = merger.resolve(relation["source"])
            target_id = merger.resolve(relation["target"])
            if source_id is None or target_id is None:
                merger.dropped_relations += 1
                merger.dropped_relation_endpoints += 1
                continue
            requested_index = int(relation["sentence_index"])
            span, grounded, recovered, actual_index = _relocate_relation(
                kept_by_seq, requested_index, str(relation.get("evidence") or ""), allowed_indices
            )
            if span is None:
                merger.dropped_relations += 1
                if kept_by_seq.get(requested_index) is None:
                    merger.dropped_relation_sentence += 1
                else:
                    merger.dropped_relation_evidence += 1
                continue
            if actual_index != requested_index:
                merger.recovered_relation_sentence_index += 1
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
        f"relocated_relations={merger.recovered_relation_sentence_index} "
        f"relocated_mentions={merger.recovered_mentions} "
        f"skipped_unreferenced={merger.skipped_unreferenced_entities} "
        f"pruned_ungrounded_isolates={merger.pruned_ungrounded_isolates} "
        f"dedup_relations={merger.deduplicated_relations}"
    )
    return merger.finalize(novel_len)
