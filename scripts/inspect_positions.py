"""Verify how merged node text_pos/time_pos are computed from their source sentences."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def main() -> None:
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_seq = {row["seq"]: row for row in kept}
    by_id = {n["id"]: n for n in graph["nodes"]}
    for name in ("Polo", "Poirot", "the window", "flower beds", "front door"):
        nodes = [n for n in graph["nodes"] if n["name"] == name]
        if not nodes:
            print(f"'{name}': 未找到")
            continue
        for node in nodes:
            src_ids = node.get("source_sentence_ids", [])
            src = [by_seq[int(s)] for s in src_ids if int(s) in by_seq]
            if src:
                mean_tp = sum(float(r["text_position"]) for r in src) / len(src)
                mean_time = sum(float(r["time_position"]) for r in src) / len(src)
                print(
                    f"{node['name']}: text_pos={node.get('text_pos'):.3f} time_pos={node.get('time_pos'):.3f} day={node.get('day')} "
                    f"| source 句数={len(src)} 句均值 text={mean_tp:.3f} time={mean_time:.3f} "
                    f"| 源句 text 位置范围={min(float(r['text_position']) for r in src):.2f}~{max(float(r['text_position']) for r in src):.2f}"
                )
            else:
                print(f"{node['name']}: 无 source 句")


if __name__ == "__main__":
    main()
