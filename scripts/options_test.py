"""Official question WITH options (A-D) vs open-ended: full-text and graph RAG, using deepseek-v4-flash."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.store import GraphStore
from novel_kg_studio.store.bm25 import BM25Index
from novel_kg_studio.store.llm_retriever import execute, plan_search, top_sentences

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"
ANNO = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "anno_103.json"
QUESTION = "How did the killer leave the scene?"


def cached_answer(client, key: str, prompt: str) -> str:
    path = OUT / "options" / f"{key}.json"
    cached = load_json(path)
    if cached:
        return cached["answer"]
    raw = ""
    for _ in range(3):
        raw = client.complete(
            "You are a careful detective-novel reader.",
            prompt,
            max_tokens=200,
        )
        if raw.strip():
            break
    save_json(path, {"answer": raw})
    return raw


def main() -> None:
    anno = json.loads(ANNO.read_text(encoding="utf-8"))[0]["questions"][0]
    options = anno["options"]
    option_text = "\n".join(f"{letter}. {text.strip()}" for letter, text in options.items())
    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    client = LLMClient(model="deepseek-v4-flash", temperature=0.0, max_tokens=1000, retries=3)
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in graph["nodes"])).encode("utf-8")).hexdigest()[:8]

    full_text = "\n".join(row["text"] for row in sorted(kept, key=lambda r: r["seq"]))
    prompt_ft = (
        f"Question: {QUESTION}\n\nOptions:\n{option_text}\n\n"
        f"Novel text:\n{full_text}\n\nAnswer with the option letter and text only, e.g. 'C. Through the window'."
    )
    ans_ft = cached_answer(client, "full_text_options_r", prompt_ft)

    store = GraphStore(graph["nodes"], graph["edges"])
    plan = plan_search(client, QUESTION, store, cache_dir=OUT / "plans", graph_fp=graph_fp)
    first, second = execute(store, plan)
    sent_hits = top_sentences(BM25Index([r["text"] for r in kept]), QUESTION, plan, k=6)
    sent_texts = [kept[i]["text"] for i, _, _ in sent_hits]
    clue_lines = []
    for node_id in first[:6]:
        node = store.by_id[node_id]
        clue_lines.append(f"- {node['name']} [{node['type']}]: {node.get('description','')} {' | '.join(node.get('evidence', [])[:2])}".strip())
    prompt_graph = (
        f"Question: {QUESTION}\n\nOptions:\n{option_text}\n\nGraph clues:\n"
        + "\n".join(clue_lines)
        + "\n\nEvidence sentences:\n" + "\n".join(f"- {t}" for t in sent_texts[:6])
        + "\n\nRules: treat 'half-open front door to create the illusion' as misdirection. "
        "Answer with the option letter and text only, e.g. 'C. Through the window'."
    )
    ans_graph = cached_answer(client, "graph_options_r", prompt_graph)

    print("官方题（有选项 A-D）deepseek-v4-flash:")
    print("  full-text 整本 + 选项:", ans_ft.strip()[:160])
    print("  图谱 RAG   + 选项:", ans_graph.strip()[:160])
    correct_ft = "window" in ans_ft.lower()
    correct_graph = "window" in ans_graph.lower()
    print(f"  判定: full-text={'对' if correct_ft else '错'} | graph={'对' if correct_graph else '错'}")


if __name__ == "__main__":
    main()
