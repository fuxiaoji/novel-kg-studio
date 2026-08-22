"""v2 evaluation: LLM-planned retrieval + sentence evidence + decoy-aware answering."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.store import GraphStore
from novel_kg_studio.store.bm25 import BM25Index
from novel_kg_studio.store.llm_retriever import execute, plan_search, top_sentences
from novel_kg_studio.store.verify import best_candidate, candidate_names, confession_sentences, verify_candidates
from masked_text_qa import GOLD, hit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def masked_store(graph: dict, mask_pos: float) -> GraphStore:
    nodes = [n for n in graph["nodes"] if float(n.get("text_pos", 1.0)) <= mask_pos]
    ids = {n["id"] for n in nodes}
    edges = [e for e in graph["edges"] if e["source"] in ids and e["target"] in ids]
    return GraphStore(nodes, edges)


def coverage_from_names(by_name, by_id, names: list[str], terms: list[str]) -> float:
    found: set[str] = set()
    for name in names:
        node_id = by_name.get(name.lower().strip())
        node = by_id.get(node_id) if node_id else None
        if node is None:
            continue
        text = " ".join([node["name"], *node.get("aliases", []), node.get("description", ""), *node.get("evidence", [])]).lower()
        for term in terms:
            if term in text:
                found.add(term)
    return len(found) / max(len(terms), 1)


def main() -> None:
    config = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    v1_path = OUT / "question_set_results.json"
    if v1_path.exists() and not (OUT / "question_set_results_v1.json").exists():
        v1_path.replace(OUT / "question_set_results_v1.json")
    by_name = {}
    by_id = {n["id"]: n for n in graph["nodes"]}
    for node in graph["nodes"]:
        by_name.setdefault(node["name"].lower().strip(), node["id"])
    sentence_index = BM25Index([row["text"] for row in kept])
    client = LLMClient(model="deepseek-chat", temperature=0.0, max_tokens=2000, retries=3)
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in graph["nodes"])).encode("utf-8")).hexdigest()[:8]

    rows = []
    for item in config.get("question_set") or []:
        question = str(item["question"])
        mask = float(item.get("mask", 1.0))
        gold = GOLD.get(question, {"answer": "?", "terms": []})
        store = masked_store(graph, mask)
        plan = plan_search(client, question, store, cache_dir=OUT / "plans", graph_fp=graph_fp)
        first, second = execute(store, plan)
        sent_hits = top_sentences(sentence_index, question, plan, k=6)
        sent_texts = [kept[i]["text"] for i, _, _ in sent_hits]
        verification = []
        if question.lower().startswith("who"):
            victim_match = re.search(r"killed\s+(.+?)[?？]", question)
            victim_name = victim_match.group(1).strip() if victim_match else ""
            candidates = candidate_names(store, first, second, plan.entity_targets, victim_name=victim_name)
            clue_lines = []
            for node_id in first[:8]:
                node = store.by_id[node_id]
                clue_lines.append(
                    f"{node['name']} [{node['type']}]: {node.get('description','')} {' | '.join(node.get('evidence', [])[:2])}".strip()
                )
            evidence = sent_texts + confession_sentences(kept, k=6)
            verification = verify_candidates(
                client,
                question,
                candidates,
                evidence,
                clue_lines,
                cache_dir=OUT / "verifications",
                cache_key=f"{question}|{mask}|{graph_fp}",
            )

        answer_key = hashlib.sha1(f"{question}|{mask}|{graph_fp}|verify_v2".encode("utf-8")).hexdigest()[:12]
        answer_path = OUT / "answers_v4" / f"{answer_key}.json"
        cached = load_json(answer_path)
        if cached:
            answer = cached["answer"]
        else:
            clue_lines = []
            for node_id in first[:6]:
                node = store.by_id[node_id]
                desc = node.get("description") or ""
                ev = " | ".join(node.get("evidence", [])[:2])
                clue_lines.append(f"- {node['name']} [{node['type']}] salience={node.get('salience', 3)}: {desc} {ev}".strip())
            prompt = (
                f"You are a reader at {mask:.0%} of the novel; only clues up to that point are allowed.\n"
                f"Question: {question}\n\nGraph clue nodes:\n"
                + "\n".join(clue_lines)
                + "\n\nEvidence sentences:\n"
                + "\n".join(f"- {t}" for t in sent_texts[:6])
            )
            if verification:
                prompt += (
                    "\n\nSuspect verification verdicts:\n"
                    + "\n".join(f"- {v['candidate']}: {v['verdict']} ({v['confidence']}) {v['reason'][:120]}" for v in verification)
                    + "\n\nIf a suspect's guilt is refuted by the evidence (e.g. a confession made to protect someone else), do not choose them."
                )
            prompt += (
                "\n\nRules: treat clues that describe an illusion/false trail "
                "(e.g. 'left the front door half-open to create the illusion') as misdirection, not the answer. "
                "Answer with the most likely answer; say what remains uncertain.\n"
                'Return strict JSON only: {"answer": "..."}'
            )
            payload = client.complete_json(
                "You are a careful detective-novel reader answering from the provided clues only.",
                prompt,
                max_tokens=900,
            )
            answer = str(payload.get("answer") or "") if isinstance(payload, dict) else str(payload)
            save_json(answer_path, {"answer": answer})

        first_names = [store.by_id[nid]["name"] for nid in first]
        second_names = [store.by_id[nid]["name"] for nid in second]
        cov_first = coverage_from_names(by_name, by_id, first_names, gold["terms"])
        cov_second = coverage_from_names(by_name, by_id, first_names + second_names, gold["terms"])
        cov_sent = len(set(gold["terms"]) & set(" ".join(sent_texts).lower().split())) / max(len(gold["terms"]), 1)
        is_hit = hit(answer, gold["terms"])
        rows.append(
            {
                "question": question,
                "mask": mask,
                "gold_answer": gold["answer"],
                "interpretation": plan.search_terms,
                "expanded_query": plan.hypothetical_clue,
                "entity_targets": plan.entity_targets,
                "search_plan": {
                    "search_terms": plan.search_terms,
                    "target_types": plan.target_types,
                    "hypothetical_clue": plan.hypothetical_clue,
                    "follow_up_terms": plan.follow_up_terms,
                },
                "first_order": first_names,
                "second_order": second_names,
                "third_order": [],
                "evidence_sentences": sent_texts[:6],
                "verifications": verification,
                "counts": {"first": len(first), "second": len(second), "third": 0},
                "gold_coverage": {
                    "first": round(cov_first, 3),
                    "first_second": round(cov_second, 3),
                    "first_second_third": round(cov_second, 3),
                    "sentences": round(cov_sent, 3),
                },
                "third_order_informative_ratio": 0.0,
                "answer": answer,
                "correct": is_hit,
            }
        )
        print(f"[{question[:40]}] mask={mask:.2f} 命中={is_hit} 覆盖一阶={cov_first:.2f} 一阶+二阶={cov_second:.2f} 句子={cov_sent:.2f}")

    save_json(v1_path, rows)
    save_json(OUT / "question_set_results_v2.json", rows)
    full = [r for r in rows if r["mask"] >= 0.99]
    acc = sum(1 for r in full if r["correct"]) / max(len(full), 1)
    print(f"\n全文本题（{len(full)} 题）v2 命中率: {acc:.0%}")
    print("saved:", v1_path, "and", OUT / "question_set_results_v2.json")


if __name__ == "__main__":
    main()
