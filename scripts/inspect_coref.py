"""Inspect pronoun-related nodes/relations in the built graph."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"

PRONOUN_TOKENS = re.compile(r"\b(he|him|his|she|her|hers|i|me|my|we|us|our|they|them|their)\b", re.IGNORECASE)
PRONOUN_NAMES = {"he", "him", "his", "she", "her", "i", "me", "my", "we", "us", "our", "they", "them", "their"}


def main() -> None:
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    nodes, edges = graph["nodes"], graph["edges"]
    by_id = {n["id"]: n for n in nodes}
    by_name = {}
    for n in nodes:
        by_name.setdefault(n["name"].lower(), []).append(n)

    print("== Jack 相关节点 ==")
    for key in ("jack", "jack renault", "mr. jack renault"):
        for n in by_name.get(key, []):
            print(f"  {n['name']} [{n['type']}] deg={n['degree']} mentions={n.get('mention_count')}")
            for ev in n.get("evidence", [])[:5]:
                print(f"      ev: {ev[:100]}")

    print("\n== 疑似代词/泛指节点（名字是代词或泛指称谓）==")
    generic = [n for n in nodes if n["name"].lower() in PRONOUN_NAMES or re.fullmatch(r"the (man|woman|girl|boy|killer|murderer|victim|narrator|doctor|servant)", n["name"].lower())]
    for n in sorted(generic, key=lambda n: -n["degree"])[:15]:
        print(f"  {n['name']} [{n['type']}] deg={n['degree']} mentions={n.get('mention_count')}")
        for ev in n.get("evidence", [])[:3]:
            print(f"      ev: {ev[:90]}")

    print("\n== 关系证据含代词、且端点是一方是泛指节点的数量 ==")
    generic_ids = {n["id"] for n in generic}
    hits = 0
    examples = []
    for e in edges:
        src = by_id[e["source"]]
        dst = by_id[e["target"]]
        if e["source"] in generic_ids or e["target"] in generic_ids:
            if PRONOUN_TOKENS.search(e.get("evidence", "")):
                hits += 1
                if len(examples) < 8:
                    examples.append((src["name"], e["type"], dst["name"], e["evidence"]))
    print("count:", hits)
    for src, rtype, dst, ev in examples:
        print(f"  {src} --{rtype}--> {dst} | {ev[:90]}")

    print("\n== 名字含代词或非常短(<2字符)的节点 ==")
    short = [n for n in nodes if len(n["name"].split()) <= 1 and n["name"].lower() in {"he", "she", "him", "her", "i", "me", "it", "they", "we", "you"}]
    print("count:", len(short))
    for n in short[:10]:
        print(f"  {n['name']} [{n['type']}] deg={n['degree']} ev={n.get('evidence', [])[:2]}")


if __name__ == "__main__":
    main()
