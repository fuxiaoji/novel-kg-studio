"""Show the official gold (answer, reasoning, clue paragraphs, gold nodes) for the exit question."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"
ANNO = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "anno_103.json"
NOVEL = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "novel_103.txt"


def paragraph(novel_text: str, number: int) -> str:
    match = re.search(rf"(?m)^\[{number}\]\s*(.*?)(?=^\[\d+\]\s|\Z)", novel_text, flags=re.DOTALL)
    return re.sub(r"\s+", " ", match.group(1)).strip()[:220] if match else "(未找到)"


def main() -> None:
    anno = json.loads(ANNO.read_text(encoding="utf-8"))[0]["questions"][0]
    novel = NOVEL.read_text(encoding="utf-8")
    print("问题:", anno["question"])
    print("官方答案:", anno["answer"], "->", anno["options"].get(anno["answer"]))
    print()
    print("官方推理步骤:")
    for idx, step in enumerate(anno.get("reasoning") or [], 1):
        print(f"  {idx}. {step.strip()}")
    print()
    print("官方线索/答案段落:")
    for num in [int(p) for p in (anno.get("clue_position") or []) if isinstance(p, (int, float)) and p >= 0] + [int(anno.get("answer_position") or -1)]:
        if num < 0:
            continue
        print(f"  [{num}] {paragraph(novel, num)}")
    print()
    analysis = json.loads((OUT / "gold_analysis.json").read_text(encoding="utf-8"))
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    by_id = {n["id"]: n for n in graph["nodes"]}
    print("金标词元:", ", ".join(analysis["gold_tokens"]))
    print()
    print("命中金标词的图节点（3D 中金色菱形高亮）:")
    for node_id in analysis["gold_node_ids"]:
        node = by_id[node_id]
        print(f"  {node['name']} [{node['type']}] deg={node['degree']} | {node['evidence'][:1]}")


if __name__ == "__main__":
    main()
