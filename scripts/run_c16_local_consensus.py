"""C16: frozen two-local-small-model confidence consensus over 20 novels."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "outputs" / "four_datasets"
OUT = BASE / "dqa_local_c16_consensus20"
VERSION = "c16-qwen35-tail-graph-agreement-else-qwen25-c12-v1"


def load(root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in root.glob("*/*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        result[row["qid"]] = row
    return result


def main() -> None:
    tail = load(BASE / "dqa_qwen35_c15_20" / "answers" / "tail")
    graph = load(BASE / "dqa_qwen35_c15_20" / "answers" / "graph")
    c12 = load(BASE / "dqa_qwen_c12_consensus20" / "answers")
    if set(tail) != set(graph) or set(tail) != set(c12) or len(tail) != 164:
        raise RuntimeError(f"incomplete inputs: tail={len(tail)} graph={len(graph)} c12={len(c12)}")
    outputs = []
    for qid, tail_row in tail.items():
        graph_row, c12_row = graph[qid], c12[qid]
        agree = tail_row.get("selected_letter") == graph_row.get("selected_letter") and tail_row.get("selected_letter") in "ABCD"
        selected = tail_row["selected_letter"] if agree else c12_row["selected_letter"]
        row = {
            "version": VERSION,
            "novel": tail_row["novel"],
            "batch": c12_row["batch"],
            "qi": tail_row["qi"],
            "qid": qid,
            "question": tail_row["question"],
            "choices": tail_row["choices"],
            "gold_letter": tail_row["gold_letter"],
            "selected_letter": selected,
            "correct": selected == tail_row["gold_letter"],
            "route": "qwen35_agreement" if agree else "qwen25_c12_fallback",
            "votes": {"qwen35_tail": tail_row.get("selected_letter"), "qwen35_graph": graph_row.get("selected_letter"), "qwen25_c12": c12_row.get("selected_letter")},
            "models": ["qwen3.5:9b", "qwen2.5:7b-32k"],
            "external_api": False,
            "mask": "unmasked",
        }
        path = OUT / "answers" / row["novel"] / f"q{int(row['qi']):02d}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
        outputs.append(row)
    summary = {}
    for name, subset in {
        "all": outputs,
        "first10": [row for row in outputs if row["batch"] == "first10"],
        "second10": [row for row in outputs if row["batch"] == "second10"],
    }.items():
        correct = sum(row["correct"] for row in subset)
        summary[name] = {"correct": correct, "total": len(subset), "accuracy": correct / len(subset)}
    result = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "version": VERSION, "rule": "use qwen3.5 tail/graph when they agree; otherwise use qwen2.5 C12", **summary}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
