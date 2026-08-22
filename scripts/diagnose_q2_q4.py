"""Diagnose why the killer/accomplice questions fail under v2 retrieval."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def main() -> None:
    rows = json.loads((OUT / "question_set_results_v2.json").read_text(encoding="utf-8"))
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    by_id = {n["id"]: n for n in graph["nodes"]}
    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        if "killed Mr. Renault" in row["question"] or "accomplice" in row["question"]:
            print("=" * 70)
            print("Q:", row["question"], "| 命中:", row["correct"], "| 金标:", row["gold_answer"])
            print("一阶:", row["first_order"])
            print("二阶:", row["second_order"])
            print("证据句:")
            for s in row.get("evidence_sentences") or []:
                print("   -", s[:120])
            print("回答:", row["answer"][:260])

    print("\n== 图谱中真凶相关节点 ==")
    patterns = ("marte", "daubreuil", "dob", "berod", "malt", "marthe", "bellodi")
    for node in graph["nodes"]:
        if any(p in node["name"].lower() for p in patterns):
            print(f"  {node['name']} [{node['type']}] salience={node.get('salience')} deg={node['degree']} desc={node.get('description','')[:60]}")
    print("\n== 含供词/凶手句子的线索 ==")
    kw = re.compile(r"killed old renault|who killed|confess|she said she was the killer|the woman who killed", re.IGNORECASE)
    for row in kept:
        if kw.search(row["text"]):
            print(f"  [{row['seq']}] pos={row['text_position']:.3f} {row['text'][:130]}")


if __name__ == "__main__":
    main()
