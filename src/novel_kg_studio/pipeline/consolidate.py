"""LLM entity consolidation: merge translation-variant character nodes into canonical entities."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from ..cache import load_json, save_json
from ..schema import norm_text


def consolidate_person_nodes(
    graph: dict[str, Any],
    client: Any,
    out_dir: Path,
    *,
    cap: int = 360,
    batch_size: int = 60,
    anchor_count: int = 12,
    resume: bool = True,
    log: Callable[[str], None] = print,
) -> tuple[dict[str, Any], dict[str, Any]]:
    persons = [
        node
        for node in graph["nodes"]
        if node["type"] == "person"
        and (node.get("degree", 0) > 0 or int(node.get("salience", 0) or 0) >= 3)
    ]
    ranked = sorted(
        persons,
        key=lambda node: (-int(node.get("degree", 0) or 0), -int(node.get("salience", 0) or 0)),
    )[:cap]
    anchors = ranked[: min(anchor_count, len(ranked))]
    anchor_ids = {node["id"] for node in anchors}
    remaining = sorted(
        (node for node in ranked if node["id"] not in anchor_ids),
        key=lambda node: norm_text(node["name"]),
    )
    local_size = max(batch_size - len(anchors), 1)
    overlap = min(8, max(local_size // 5, 0))
    step = max(local_size - overlap, 1)
    batches: list[list[dict[str, Any]]] = []
    if remaining:
        for start in range(0, len(remaining), step):
            local = remaining[start : start + local_size]
            if not local:
                break
            batches.append([*anchors, *local])
            if start + local_size >= len(remaining):
                break
    elif anchors:
        batches = [anchors]

    groups: list[dict[str, Any]] = []
    empty_batches = 0
    for batch_index, batch in enumerate(batches):
        names = [node["name"] for node in batch]
        fp = hashlib.sha1(("consolidate_v2\n" + "\n".join(names)).encode("utf-8")).hexdigest()[:12]
        cache_path = out_dir / "consolidation" / f"groups_v2_{batch_index:02d}_{fp}.json"
        cached = load_json(cache_path) if resume else None
        if cached is not None:
            payload = cached
        else:
            payload = client.complete_json(
                "You resolve duplicate character entities in machine-translated detective novels.",
                (
                    "Find ONLY names that definitely refer to the same person. Focus on honorific changes, "
                    "minor spelling/romanization variants, and a full name versus surname. Never merge relatives "
                    "or merely similar names. Return only groups with at least two members.\n\nNames:\n"
                    + "\n".join(f"- {name}" for name in names)
                    + '\n\nReturn strict JSON only: {"groups":['
                    '{"canonical":"chosen name","members":["exact listed name","exact listed name"]}'
                    "]}"
                ),
                max_tokens=2500,
            )
            save_json(cache_path, payload)
        batch_groups = payload if isinstance(payload, list) else (payload.get("groups") or [] if isinstance(payload, dict) else [])
        valid = [group for group in batch_groups if isinstance(group, dict)]
        if not valid:
            empty_batches += 1
        groups.extend(valid)
    log(
        f"[consolidate] {len(groups)} candidate groups from {len(ranked)} person names "
        f"across {len(batches)} batches (empty={empty_batches})"
    )

    node_by_name = {norm_text(node["name"]): node["id"] for node in graph["nodes"]}
    by_id = {node["id"]: node for node in graph["nodes"]}
    member_to_canonical: dict[str, str] = {}
    merged = 0
    group_examples = []
    for group in groups:
        members = [str(member) for member in (group.get("members") or []) if str(member).strip()]
        ids = [node_by_name.get(norm_text(member)) for member in members]
        ids = list(dict.fromkeys(node_id for node_id in ids if node_id))
        if len(ids) <= 1:
            continue
        existing_canonicals = {
            member_to_canonical[node_id]
            for node_id in ids
            if node_id in member_to_canonical
        }
        # Permit an anchor to collect variants across overlapping batches, but
        # reject a transitive bridge whose prior canonical is absent here.
        if len(existing_canonicals) > 1 or any(canonical not in ids for canonical in existing_canonicals):
            continue
        canonical_id = (
            next(iter(existing_canonicals))
            if existing_canonicals
            else max(ids, key=lambda node_id: by_id[node_id].get("degree", 0))
        )
        for node_id in ids:
            if node_id != canonical_id and node_id not in member_to_canonical:
                member_to_canonical[node_id] = canonical_id
                merged += 1
        group_examples.append(
            (by_id[canonical_id]["name"], [by_id[node_id]["name"] for node_id in ids if node_id != canonical_id][:4])
        )

    base_stats = {
        "nodes_before": len(graph["nodes"]),
        "candidate_persons": len(ranked),
        "batches": len(batches),
        "empty_batches": empty_batches,
        "candidate_groups": len(groups),
    }
    if not member_to_canonical:
        return graph, {
            **base_stats,
            "nodes_after": len(graph["nodes"]),
            "merged_nodes": 0,
            "edges_after": len(graph["edges"]),
            "group_examples": [],
        }

    kept_nodes: dict[str, dict] = {}
    for node in graph["nodes"]:
        target = member_to_canonical.get(node["id"], node["id"])
        if target != node["id"]:
            continue
        kept_nodes[node["id"]] = dict(node)
    for source_id, canonical_id in member_to_canonical.items():
        source = by_id[source_id]
        canonical = kept_nodes[canonical_id]
        for evidence in source.get("evidence", []):
            if evidence not in canonical["evidence"]:
                canonical["evidence"].append(evidence)
        canonical["evidence"] = canonical["evidence"][:6]
        for alias in [source["name"], *source.get("aliases", [])]:
            if alias not in canonical["aliases"]:
                canonical["aliases"].append(alias)
        if len(str(source.get("description") or "")) > len(str(canonical.get("description") or "")):
            canonical["description"] = source.get("description", "")
        canonical["salience"] = max(
            int(canonical.get("salience", 3) or 3), int(source.get("salience", 3) or 3)
        )
        canonical["mention_count"] = int(canonical.get("mention_count", 0) or 0) + int(
            source.get("mention_count", 0) or 0
        )

    edges: list[dict] = []
    seen: set[tuple] = set()
    for edge in graph["edges"]:
        source = member_to_canonical.get(edge["source"], edge["source"])
        target = member_to_canonical.get(edge["target"], edge["target"])
        if source == target:
            continue
        updated = dict(edge, source=source, target=target)
        key = (source, target, updated["type"], norm_text(updated.get("evidence", "")))
        if key in seen:
            continue
        seen.add(key)
        edges.append(updated)

    degree: dict[str, int] = {}
    for edge in edges:
        degree[edge["source"]] = degree.get(edge["source"], 0) + 1
        degree[edge["target"]] = degree.get(edge["target"], 0) + 1
    nodes = list(kept_nodes.values())
    for node in nodes:
        node["degree"] = degree.get(node["id"], 0)
    consolidated = {**graph, "nodes": nodes, "edges": edges}
    stats = {
        **base_stats,
        "nodes_after": len(nodes),
        "merged_nodes": merged,
        "edges_after": len(edges),
        "group_examples": group_examples[:12],
    }
    log(f"[consolidate] nodes {stats['nodes_before']} -> {stats['nodes_after']} (merged {merged})")
    return consolidated, stats
