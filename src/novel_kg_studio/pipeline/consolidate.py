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
    cap: int = 150,
    resume: bool = True,
    log: Callable[[str], None] = print,
) -> tuple[dict[str, Any], dict[str, Any]]:
    persons = [
        n
        for n in graph["nodes"]
        if n["type"] == "person" and (n.get("degree", 0) > 0 or int(n.get("salience", 0) or 0) >= 3)
    ]
    persons.sort(key=lambda n: (-int(n.get("salience", 0) or 0), -n.get("degree", 0)))
    persons = persons[:cap]
    names = [n["name"] for n in persons]
    fp = hashlib.sha1("\n".join(names).encode("utf-8")).hexdigest()[:12]
    cache_path = out_dir / "consolidation" / f"groups_{fp}.json"
    cached = load_json(cache_path) if resume else None
    if cached:
        groups = cached
    else:
        payload = client.complete_json(
            "You are an expert in character entity resolution for machine-translated novels.",
            (
                "These names may refer to the same character through translation variants. "
                "Group names that are definitely the SAME person. Do not merge distinct characters.\n\n"
                "Names:\n"
                + "\n".join(f"- {name}" for name in names)
                + "\n\nReturn strict JSON only: "
                '[{"canonical":"chosen canonical name","members":["variant","variant"]}] '
                "(include single-member groups only if a name clearly has no variants)."
            ),
            max_tokens=2500,
        )
        groups = [g for g in (payload if isinstance(payload, list) else payload.get("groups") or []) if isinstance(g, dict)]
        save_json(cache_path, groups)
    log(f"[consolidate] {len(groups)} groups from {len(names)} person names")

    node_by_name = {norm_text(n["name"]): n["id"] for n in graph["nodes"]}
    by_id = {n["id"]: n for n in graph["nodes"]}
    member_to_canonical: dict[str, str] = {}
    merged = 0
    group_examples = []
    consumed: set[str] = set()
    for group in groups:
        members = [str(m) for m in (group.get("members") or []) if str(m).strip()]
        if not members:
            continue
        ids = [node_by_name.get(norm_text(m)) for m in members]
        ids = [nid for nid in ids if nid and nid not in consumed]
        if len(ids) <= 1:
            continue
        canonical_id = max(ids, key=lambda nid: by_id[nid].get("degree", 0))
        for nid in ids:
            if nid != canonical_id:
                member_to_canonical[nid] = canonical_id
                merged += 1
        consumed.update(ids)
        group_examples.append((by_id[canonical_id]["name"], [by_id[nid]["name"] for nid in ids if nid != canonical_id][:4]))

    if not member_to_canonical:
        return graph, {"nodes_before": len(graph["nodes"]), "merged_nodes": 0, "groups": []}

    kept_nodes: dict[str, dict] = {}
    for node in graph["nodes"]:
        target = member_to_canonical.get(node["id"], node["id"])
        if target != node["id"]:
            continue
        node = dict(node)
        kept_nodes[node["id"]] = node
    for source_id, canonical_id in member_to_canonical.items():
        source = by_id[source_id]
        canonical = kept_nodes[canonical_id]
        for ev in source.get("evidence", []):
            if ev not in canonical["evidence"]:
                canonical["evidence"].append(ev)
        canonical["evidence"] = canonical["evidence"][:6]
        for alias in source.get("aliases", []):
            if alias not in canonical["aliases"]:
                canonical["aliases"].append(alias)
        if len(str(source.get("description") or "")) > len(str(canonical.get("description") or "")):
            canonical["description"] = source.get("description", "")
        canonical["salience"] = max(int(canonical.get("salience", 3) or 3), int(source.get("salience", 3) or 3))
        canonical["mention_count"] = int(canonical.get("mention_count", 0) or 0) + int(source.get("mention_count", 0) or 0)

    edges: list[dict] = []
    seen: set[tuple] = set()
    for edge in graph["edges"]:
        source = member_to_canonical.get(edge["source"], edge["source"])
        target = member_to_canonical.get(edge["target"], edge["target"])
        if source == target:
            continue
        edge = dict(edge, source=source, target=target)
        key = (source, target, edge["type"], norm_text(edge.get("evidence", "")))
        if key in seen:
            continue
        seen.add(key)
        edges.append(edge)

    degree: dict[str, int] = {}
    for edge in edges:
        degree[edge["source"]] = degree.get(edge["source"], 0) + 1
        degree[edge["target"]] = degree.get(edge["target"], 0) + 1
    nodes = list(kept_nodes.values())
    for node in nodes:
        node["degree"] = degree.get(node["id"], 0)
    consolidated = {**graph, "nodes": nodes, "edges": edges}
    stats = {
        "nodes_before": len(graph["nodes"]),
        "nodes_after": len(nodes),
        "merged_nodes": merged,
        "edges_after": len(edges),
        "group_examples": group_examples[:12],
    }
    log(f"[consolidate] nodes {stats['nodes_before']} -> {stats['nodes_after']} (merged {merged})")
    return consolidated, stats
