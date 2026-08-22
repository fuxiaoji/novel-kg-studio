"""Check which gold nodes were built vs retrieved for the official exit question."""

from __future__ import annotations

import json
from pathlib import Path

from novel_kg_studio.schema import norm_text

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def main() -> None:
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    rows = json.loads((OUT / "question_set_results.json").read_text(encoding="utf-8"))
    analysis = json.loads((OUT / "gold_analysis.json").read_text(encoding="utf-8"))
    by_id = {n["id"]: n for n in graph["nodes"]}
    official = next(r for r in rows if "killer leave the scene" in r["question"])

    order_names = {
        "first": set(official["first_order"]),
        "second": set(official["second_order"]),
        "third": set(official["third_order"]),
    }
    print("官方题检索顺序: 一阶", len(order_names["first"]), "二阶", len(order_names["second"]), "三阶", len(order_names["third"]))
    print()
    print("金标节点(40个)中，构建/检索情况:")
    built_retrieved = 0
    built_missed = 0
    for node_id in analysis["gold_node_ids"]:
        node = by_id[node_id]
        name = node["name"]
        where = next((k for k in ("first", "second", "third") if name in order_names[k]), "未检索到")
        if where != "未检索到":
            built_retrieved += 1
        else:
            built_missed += 1
        print(f"  {name} [{node['type']}] -> {where}")
    print(f"\n已构建且被检索到: {built_retrieved} | 已构建但未检索到: {built_missed}")

    # Per-question gold coverage sanity check for Q1
    terms = ["window", "climb"]
    covered = set()
    for name in official["first_order"] + official["second_order"]:
        text = " ".join([name]).lower()
        for term in terms:
            if term in text:
                covered.add(term)
    print(f"\nQ1 金标词覆盖(名字层面) 一阶+二阶: {len(covered)}/{len(terms)} = {len(covered)/len(terms):.2f}")


if __name__ == "__main__":
    main()
