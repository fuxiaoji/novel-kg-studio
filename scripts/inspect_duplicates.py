"""Check whether characters appear as multiple nodes across the three novels' graphs."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"
NOVELS = {"103": OUT, "104": OUT / "novel_104", "117": OUT / "novel_117"}


def norm(name: str) -> str:
    return " ".join(name.lower().replace("mr.", "mr").replace("mrs.", "mrs").replace("'", "").split())


def main() -> None:
    for novel_id, out_dir in NOVELS.items():
        graph = json.loads((out_dir / "graph.json").read_text(encoding="utf-8"))
        persons = [n for n in graph["nodes"] if n["type"] == "person"]
        by_norm: dict[str, list] = {}
        for node in persons:
            by_norm.setdefault(norm(node["name"]), []).append(node)
        dup_norm = {k: v for k, v in by_norm.items() if len(v) > 1}
        # suspicious near-duplicates: distinct normalized names that share a significant token
        suspicious = []
        names = sorted(by_norm)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                if a == b or a in b or b in a:
                    suspicious.append((a, b))
        top = sorted(persons, key=lambda n: -n.get("mention_count", 0))[:5]
        print(f"novel {novel_id}: person 节点 {len(persons)} 个")
        print(f"  完全同名重复组: {len(dup_norm)} 组")
        print(f"  疑似包含关系重复(如 Poirot/Hercule Poirot): {len(suspicious)} 对")
        for a, b in suspicious[:4]:
            print(f"    ? {a}  ~  {b}")
        print("  高频人物（每个应是一个节点）:")
        for node in top:
            print(f"    {node['name']}: mentions={node.get('mention_count')} deg={node['degree']} aliases={node.get('aliases', [])[:3]}")
        print()


if __name__ == "__main__":
    main()
