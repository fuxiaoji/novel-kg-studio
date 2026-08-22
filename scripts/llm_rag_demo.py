"""Demonstrate LLM query understanding on top of the graph RAG.

Step 1: LLM reads the question + a glossary of high-degree graph nodes and
        explains what ambiguous mentions (scene / killer) refer to, and emits
        an expanded query + entity targets.
Step 2: hybrid retrieval = BM25(question) + BM25(expanded) + target boost,
        then first/second-order graph expansion.
"""

from __future__ import annotations

import json
from pathlib import Path

from novel_kg_studio.cache import save_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.store import GraphStore

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"
QUESTION = "How did the killer leave the scene?"

GLOSSARY_PROMPT = """You are using a knowledge-graph retrieval framework to answer a question about a detective novel.

Question: {question}

The knowledge graph contains these notable nodes (name | type | degree | evidence):
{glossary}

Tasks:
1. Ground every ambiguous mention in the question against these graph nodes
   (e.g. "scene" -> which node, "killer" -> which node).
2. Produce an expanded retrieval query that combines the question with the
   grounded terms so a lexical search can find the right clue nodes.
3. List the exact node names (from the glossary) the question points to.

Return strict JSON only:
{{
  "interpretation": "short explanation of what each key mention refers to in the novel",
  "expanded_query": "one short retrieval query",
  "entity_targets": ["exact node names from the glossary"]
}}"""


def main() -> None:
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    nodes, edges = graph["nodes"], graph["edges"]
    store = GraphStore(nodes, edges)

    baseline = store.retrieve(QUESTION, k1=8, k2=12)
    by_id = {n["id"]: n for n in nodes}

    glossary_nodes = sorted(nodes, key=lambda n: -n["degree"])[:80]
    glossary_lines = [
        f"- {n['name']} | {n['type']} | degree={n['degree']} | {(n.get('evidence') or [''])[0][:70]}"
        for n in glossary_nodes
    ]
    client = LLMClient(model="deepseek-chat", temperature=0.0, max_tokens=2000, retries=3)
    payload = client.complete_json(
        "You are an expert detective-novel QA researcher.",
        GLOSSARY_PROMPT.format(question=QUESTION, glossary="\n".join(glossary_lines)),
    )

    interpretation = str(payload.get("interpretation") or "")
    expanded = str(payload.get("expanded_query") or "")
    targets = [str(t) for t in (payload.get("entity_targets") or []) if str(t)]

    q_scores = store.index.score(QUESTION)
    e_scores = store.index.score(expanded)
    scores = q_scores + 1.5 * e_scores
    name_to_idx = {node["name"]: i for i, node in enumerate(store.nodes)}
    for target in targets:
        idx = name_to_idx.get(target)
        if idx is not None:
            scores[idx] += 2.5
    order = scores.argsort()[::-1]
    first_order = [store.nodes[i]["id"] for i in order if scores[i] > 0][:8]
    first_set = set(first_order)
    second_scores: dict[str, float] = {}
    for node_id in first_order:
        for neighbor, edge in store.adj.get(node_id, []):
            if neighbor in first_set:
                continue
            second_scores[neighbor] = max(second_scores.get(neighbor, 0.0), float(edge.get("confidence") or 0.0))
    second_order = sorted(second_scores, key=lambda nid: (-second_scores[nid], nid))[:12]
    expanded_result = store._subgraph(list(first_set) + second_order)
    expanded_chains = store._chains(first_order, second_order)

    print("=" * 70)
    print("LLM 查询理解:")
    print(interpretation)
    print()
    print("扩展查询:", expanded)
    print("实体目标:", targets)
    print()
    print("基线 BM25 一阶:", [by_id[nid]["name"] for nid in baseline.first_order])
    print("LLM 扩展后一阶:", [by_id[nid]["name"] for nid in first_order])
    print("LLM 扩展后二阶:", [by_id[nid]["name"] for nid in second_order])
    print()
    print("线索链（含证据）:")
    for chain in expanded_chains[:5]:
        linked = [(item["name"], item["edge_type"]) for item in chain["linked"]]
        print(f"  {chain['clue']} [{chain['type']}] -> {linked}")
        for ev in chain["evidence"][:2]:
            print(f"      ev: {ev[:110]}")

    payload_out = {
        "question": QUESTION,
        "interpretation": interpretation,
        "expanded_query": expanded,
        "entity_targets": targets,
        "baseline_first_order": [by_id[nid]["name"] for nid in baseline.first_order],
        "expanded_first_order": [by_id[nid]["name"] for nid in first_order],
        "expanded_second_order": [by_id[nid]["name"] for nid in second_order],
        "chains": expanded_chains,
        "subgraph": expanded_result,
    }
    save_json(OUT / "llm_rag.json", payload_out)
    print()
    print("saved:", OUT / "llm_rag.json")


if __name__ == "__main__":
    main()
