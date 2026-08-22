"""Print a summary of the demo outputs (graph + retrieval)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def main() -> None:
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    nodes, edges = graph["nodes"], graph["edges"]
    print("nodes:", len(nodes), "edges:", len(edges))
    print("node types:", dict(Counter(n["type"] for n in nodes)))
    print("edge types:", dict(Counter(e["type"] for e in edges).most_common(12)))
    print("top degree:")
    for node in sorted(nodes, key=lambda n: -n["degree"])[:12]:
        print(f"  {node['name']} [{node['type']}] deg={node['degree']} day={node.get('day')} ev={node['evidence'][:1]}")
    retrieval = json.loads((OUT / "retrieval.json").read_text(encoding="utf-8"))
    by_id = {n["id"]: n for n in nodes}
    print("question:", retrieval["question"])
    print("first_order:")
    for node_id in retrieval["first_order"]:
        node = by_id[node_id]
        print(f"  {node['name']} [{node['type']}] ev={node['evidence'][:1]}")
    print("second_order:")
    for node_id in retrieval["second_order"]:
        node = by_id[node_id]
        print(f"  {node['name']} [{node['type']}]")
    print("chain sample:")
    for chain in retrieval["chains"][:3]:
        print("  clue=", chain["clue"], "linked=", [(item["name"], item["edge_type"]) for item in chain["linked"]])
    for name in ("the window", "window", "front door", "the front door"):
        matches = [n for n in nodes if n["name"] == name]
        if matches:
            node = matches[0]
            rel_edges = [e for e in edges if e["source"] == node["id"] or e["target"] == node["id"]]
            print(f"\nnode {name!r}: deg={node['degree']} edges={len(rel_edges)}")
            for edge in rel_edges[:8]:
                other = edge["target"] if edge["source"] == node["id"] else edge["source"]
                print(f"  --{edge['type']}--> {by_id[other]['name']}")


if __name__ == "__main__":
    main()
