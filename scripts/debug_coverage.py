"""Debug: why Q1 coverage looked like 0.0."""

from __future__ import annotations

import json
from pathlib import Path

from novel_kg_studio.schema import norm_text

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def main() -> None:
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    rows = json.loads((OUT / "question_set_results.json").read_text(encoding="utf-8"))
    official = next(r for r in rows if "killer leave the scene" in r["question"])
    by_name = {}
    by_id = {n["id"]: n for n in graph["nodes"]}
    for node in graph["nodes"]:
        by_name.setdefault(norm_text(node["name"]), node["id"])
    print("Q1 first_order:", official["first_order"])
    for name in official["first_order"]:
        node_id = by_name.get(norm_text(name))
        node = by_id.get(node_id) if node_id else None
        print(f"  {name!r} -> node={node['name'] if node else None} ev={node.get('evidence', [])[:1] if node else None}")
    terms = ["window", "climb"]
    covered = set()
    for name in official["first_order"] + official["second_order"]:
        node_id = by_name.get(norm_text(name))
        node = by_id.get(node_id) if node_id else None
        if node is None:
            continue
        text = " ".join([node["name"], *node.get("aliases", []), *node.get("evidence", [])]).lower()
        for term in terms:
            if term in text:
                covered.add(term)
    print("covered:", covered, "=>", len(covered) / len(terms))


if __name__ == "__main__":
    main()
