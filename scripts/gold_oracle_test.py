"""Oracle test: feed gold clues/nodes directly to the LLM — can it reach 100%?"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"
ANNO = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "anno_103.json"
NOVEL = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "novel_103.txt"
QUESTION = "How did the killer leave the scene?"


def paragraph(novel_text: str, number: int) -> str:
    match = re.search(rf"(?m)^\[{number}\]\s*(.*?)(?=^\[\d+\]\s|\Z)", novel_text, flags=re.DOTALL)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def main() -> None:
    anno = json.loads(ANNO.read_text(encoding="utf-8"))[0]["questions"][0]
    novel = NOVEL.read_text(encoding="utf-8")
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    by_id = {n["id"]: n for n in graph["nodes"]}
    analysis = json.loads((OUT / "gold_analysis.json").read_text(encoding="utf-8"))

    gold_nodes = []
    for node_id in analysis.get("gold_node_ids", [])[:20]:
        node = by_id[node_id]
        evidence = " | ".join(node.get("evidence", [])[:2])
        if evidence:
            gold_nodes.append(f"- {node['name']} [{node['type']}]: {evidence[:160]}")

    clue_numbers = [int(p) for p in (anno.get("clue_position") or []) if isinstance(p, (int, float)) and p >= 0] + [int(anno.get("answer_position") or -1)]
    clue_paragraphs = [f"[{n}] {paragraph(novel, n)}" for n in clue_numbers if n >= 0 and paragraph(novel, n)]
    reasoning = [str(s).strip() for s in (anno.get("reasoning") or [])]

    variants = {
        "A_金标节点": "Knowledge-graph clues:\n" + "\n".join(gold_nodes),
        "B_官方线索段": "Novel excerpt clues:\n" + "\n".join(clue_paragraphs),
        "C_线索段+官方推理": "Novel excerpt clues:\n" + "\n".join(clue_paragraphs) + "\n\nOfficial reasoning steps:\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(reasoning)),
    }

    client = LLMClient(model="deepseek-chat", temperature=0.0, max_tokens=800, retries=3)
    for name, clues in variants.items():
        key = hashlib.sha1(f"{QUESTION}|{name}".encode("utf-8")).hexdigest()[:12]
        path = OUT / "oracle" / f"{key}.json"
        cached = load_json(path)
        if cached:
            answer = cached["answer"]
        else:
            payload = client.complete_json(
                "You are an expert detective-novel reader.",
                (
                    f"Question: {QUESTION}\n\n"
                    f"{clues}\n\n"
                    "Answer with the single most likely option (front door / backdoor / window / roof) "
                    "and one sentence of reasoning.\n"
                    'Return strict JSON only: {"answer": "..."}'
                ),
            )
            answer = str(payload.get("answer") or "") if isinstance(payload, dict) else str(payload)
            save_json(path, {"answer": answer})
        correct = "window" in str(answer).lower()
        print(f"[{name}] 正确={'是' if correct else '否'} | {answer[:200]}")


if __name__ == "__main__":
    main()
