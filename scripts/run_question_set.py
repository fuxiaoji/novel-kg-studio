"""Masked LLM QA over the novel question set + third-order expansion evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.store import GraphStore
from novel_kg_studio.store.bm25 import tokenize

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"
ANNO = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "anno_103.json"
STOP = {"the", "a", "an", "of", "to", "in", "on", "at", "was", "were", "is", "are", "and", "or", "did", "do", "what", "who", "how"}

UNDERSTAND_SYSTEM = "You are an expert detective-novel QA researcher."
ANSWER_SYSTEM = "You are a careful detective-novel reader answering from visible clues only."


def masked_store(graph: dict, mask_pos: float) -> GraphStore:
    nodes = [n for n in graph["nodes"] if float(n.get("text_pos", 1.0)) <= mask_pos]
    ids = {n["id"] for n in nodes}
    edges = [e for e in graph["edges"] if e["source"] in ids and e["target"] in ids]
    return GraphStore(nodes, edges)


def hybrid_orders(store: GraphStore, question: str, expanded: str, targets: list[str], *, k1: int = 8, k2: int = 12, k3: int = 15):
    scores = store.index.score(question) + 1.5 * store.index.score(expanded)
    name_to_idx = {node["name"]: i for i, node in enumerate(store.nodes)}
    for target in targets:
        idx = name_to_idx.get(target)
        if idx is not None:
            scores[idx] += 2.5
    order = scores.argsort()[::-1]
    first = [store.nodes[i]["id"] for i in order if scores[i] > 0][:k1]
    first_set = set(first)
    second_scores: dict[str, float] = {}
    for node_id in first:
        for neighbor, edge in store.adj.get(node_id, []):
            if neighbor in first_set:
                continue
            second_scores[neighbor] = max(second_scores.get(neighbor, 0.0), float(edge.get("confidence") or 0.0))
    second = sorted(second_scores, key=lambda nid: (-second_scores[nid], nid))[:k2]
    second_set = set(second)
    third_scores: dict[str, float] = {}
    for node_id in second:
        for neighbor, edge in store.adj.get(node_id, []):
            if neighbor in first_set or neighbor in second_set or neighbor in third_scores:
                continue
            third_scores[neighbor] = max(third_scores.get(neighbor, 0.0), float(edge.get("confidence") or 0.0))
    third = sorted(third_scores, key=lambda nid: (-third_scores[nid], nid))[:k3]
    return first, second, third


def gold_tokens() -> set[str]:
    if not ANNO.exists():
        return set()
    data = json.loads(ANNO.read_text(encoding="utf-8"))
    reasoning = data[0]["questions"][0].get("reasoning") or []
    return {t for t in tokenize(" ".join(reasoning)) if t not in STOP and len(t) > 1}


def coverage(store: GraphStore, node_ids: list[str], gold: set[str]) -> float:
    covered: set[str] = set()
    for node_id in node_ids:
        node = store.by_id.get(node_id)
        if node is None:
            continue
        text = " ".join([node.get("name", ""), *node.get("aliases", []), *node.get("evidence", [])])
        covered |= {t for t in tokenize(text) if t not in STOP and len(t) > 1}
    return len(covered & gold) / max(len(gold), 1)


def informative_ratio(store: GraphStore, node_ids: list[str], hubs: set[str]) -> float:
    if not node_ids:
        return 0.0
    informative = [
        nid
        for nid in node_ids
        if store.by_id[nid]["type"] in {"clue_object", "location", "time_anchor", "event"}
        and store.by_id[nid]["name"] not in hubs
    ]
    return len(informative) / len(node_ids)


def main() -> None:
    config = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    full_store = GraphStore(graph["nodes"], graph["edges"])
    hubs = {n["name"] for n in sorted(graph["nodes"], key=lambda n: -n["degree"])[:8]}
    gold = gold_tokens()
    client = LLMClient(model="deepseek-chat", temperature=0.0, max_tokens=2500, retries=3)
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in graph["nodes"])).encode("utf-8")).hexdigest()[:8]

    rows = []
    for item in config.get("question_set") or []:
        question = str(item["question"])
        mask_pos = float(item.get("mask", 1.0))
        store = masked_store(graph, mask_pos)
        cache_key = hashlib.sha1(f"{question}|{mask_pos}|{graph_fp}".encode("utf-8")).hexdigest()[:12]
        cache_path = OUT / "question_set" / f"{cache_key}.json"
        cached = load_json(cache_path)
        if cached:
            understanding = cached
        else:
            glossary_nodes = sorted(store.nodes, key=lambda n: -n["degree"])[:80]
            glossary = "\n".join(
                f"- {n['name']} | {n['type']} | degree={n['degree']} | {(n.get('evidence') or [''])[0][:70]}"
                for n in glossary_nodes
            )
            payload = client.complete_json(
                UNDERSTAND_SYSTEM,
                (
                    "You are using a knowledge-graph retrieval framework. Question: "
                    + question
                    + "\n\nNotable graph nodes (name | type | degree | evidence):\n"
                    + glossary
                    + "\n\n1) Ground ambiguous mentions to exact node names. 2) Produce an expanded retrieval query. "
                    '3) List exact target node names. Return strict JSON only: {"interpretation": "...", '
                    '"expanded_query": "...", "entity_targets": ["..."]}'
                ),
            )
            understanding = {
                "interpretation": str(payload.get("interpretation") or ""),
                "expanded_query": str(payload.get("expanded_query") or ""),
                "entity_targets": [str(t) for t in (payload.get("entity_targets") or []) if str(t)],
            }
            save_json(cache_path, understanding)

        first, second, third = hybrid_orders(
            store,
            question,
            understanding["expanded_query"],
            understanding["entity_targets"],
            k1=int((config.get("retrieval") or {}).get("k1") or 8),
            k2=int((config.get("retrieval") or {}).get("k2") or 12),
            k3=int((config.get("retrieval") or {}).get("k3") or 15),
        )
        cover_2 = coverage(store, first + second, gold)
        cover_3 = coverage(store, first + second + third, gold)
        info_3 = informative_ratio(store, third, hubs)

        clue_lines = []
        for node_id in first[:6]:
            node = store.by_id[node_id]
            clue_lines.append(f"- {node['name']} [{node['type']}]: {' | '.join(node.get('evidence', [])[:2])}")
        answer = client.complete_json(
            ANSWER_SYSTEM,
            (
                f"You are a reader at {mask_pos:.0%} of the novel; you may only use clues up to that point.\n"
                f"Question: {question}\n\nVisible clues:\n"
                + "\n".join(clue_lines)
                + "\n\nAnswer concisely (1-3 sentences). State what is known and what remains uncertain. "
                'Return strict JSON only: {"answer": "..."}'
            ),
            max_tokens=800,
        )
        answer_text = str(answer.get("answer") or "") if isinstance(answer, dict) else str(answer)

        row = {
            "question": question,
            "mask": mask_pos,
            "interpretation": understanding["interpretation"],
            "expanded_query": understanding["expanded_query"],
            "entity_targets": understanding["entity_targets"],
            "first_order": [store.by_id[nid]["name"] for nid in first],
            "second_order": [store.by_id[nid]["name"] for nid in second],
            "third_order": [store.by_id[nid]["name"] for nid in third],
            "counts": {"first": len(first), "second": len(second), "third": len(third)},
            "gold_coverage": {"first_second": round(cover_2, 3), "first_second_third": round(cover_3, 3)},
            "third_order_informative_ratio": round(info_3, 3),
            "answer": answer_text,
        }
        rows.append(row)
        print(f"[ok] mask={mask_pos:.2f} {question[:45]} | 1st={len(first)} 2nd={len(second)} 3rd={len(third)} "
              f"cov2={cover_2:.2f} cov3={cover_3:.2f} info3={info_3:.2f}")

    save_json(OUT / "question_set_results.json", rows)
    print()
    print("== 三阶扩散评估 ==")
    total_third = sum(r["counts"]["third"] for r in rows)
    total_info = sum(round(r["third_order_informative_ratio"] * r["counts"]["third"]) for r in rows)
    print(f"3rd-order nodes total={total_third} informative={total_info} "
          f"({total_info / max(total_third, 1):.0%})")
    avg_cov_gain = sum(r["gold_coverage"]["first_second_third"] - r["gold_coverage"]["first_second"] for r in rows) / max(len(rows), 1)
    print(f"avg gold-coverage gain from 3rd order: {avg_cov_gain:.3f}")
    print("saved:", OUT / "question_set_results.json")


if __name__ == "__main__":
    main()
