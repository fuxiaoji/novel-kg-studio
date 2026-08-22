"""v4 evaluation: same as v2 + LLM reads the FULL source sentences behind each node."""

from __future__ import annotations

import hashlib
import json
import argparse
import re
from pathlib import Path

import yaml

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import extract_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.store import GraphStore
from novel_kg_studio.store.bm25 import BM25Index
from novel_kg_studio.store.llm_retriever import execute, plan_search, top_sentences
from novel_kg_studio.store.verify import candidate_names, confession_sentences, verify_candidates
from eval_v2 import masked_store
from masked_text_qa import GOLD, hit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="deepseek-chat")
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sentence_index = BM25Index([row["text"] for row in kept])
    client = LLMClient(model=args.model, temperature=0.0, max_tokens=2000, retries=3)
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in graph["nodes"])).encode("utf-8")).hexdigest()[:8]

    rows = []
    for item in config.get("question_set") or []:
        question = str(item["question"])
        mask = float(item.get("mask", 1.0))
        gold = GOLD.get(question, {"answer": "?", "terms": []})
        choices = item.get("choices") or []
        opt_block = "Options:\n" + "\n".join(f"{chr(65 + i)}. {str(c).strip()}" for i, c in enumerate(choices)) if choices else ""
        opt_hash = hashlib.sha1(opt_block.encode("utf-8")).hexdigest()[:6]
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

        # Node -> full source sentences expansion
        expanded: list[str] = []
        seen_seqs: set[int] = set()
        for node_id in first[:8]:
            node = store.by_id.get(node_id)
            if node is None:
                continue
            for seq in node.get("source_sentence_ids", [])[:4]:
                seq = int(seq)
                if seq not in seen_seqs and 0 <= seq < len(kept):
                    seen_seqs.add(seq)
                    expanded.append(kept[seq]["text"])
        expanded = expanded[:10]

        answer_key = hashlib.sha1(f"{question}|{mask}|{graph_fp}|expand_v1|{opt_hash}|{args.model}".encode("utf-8")).hexdigest()[:12]
        answer_path = OUT / "answers_v5" / f"{answer_key}.json"
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
                f"Question: {question}\n\n{opt_block}\n\nGraph clue nodes:\n"
                + "\n".join(clue_lines)
                + "\n\nEvidence sentences:\n"
                + "\n".join(f"- {t}" for t in sent_texts[:6])
            )
            if expanded:
                prompt += "\n\n节点背后原文（检索到的节点所引用的完整句子）:\n" + "\n".join(f"- {t}" for t in expanded)
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
                "When options are given, answer with the option letter and its text (e.g. 'C. Through the window').\n"
                'Return strict JSON only: {"answer": "..."}'
            )
            answer = ""
            try:
                raw = client.complete(
                    "You are a careful detective-novel reader answering from the provided clues only.",
                    prompt,
                    max_tokens=900,
                )
                try:
                    parsed = extract_json(raw)
                    answer = str(parsed.get("answer") or "") if isinstance(parsed, dict) else str(parsed)
                except ValueError:
                    answer = raw.strip()
            except Exception as exc:
                print(f"[warn] answer generation failed for {question[:40]}: {type(exc).__name__}")
            save_json(answer_path, {"answer": answer})

        first_names = [store.by_id[nid]["name"] for nid in first]
        second_names = [store.by_id[nid]["name"] for nid in second]
        is_hit = hit(answer, gold["terms"])
        rows.append(
            {
                "question": question,
                "mask": mask,
                "gold_answer": gold["answer"],
                "choices": choices,
                "first_order": first_names,
                "second_order": second_names,
                "third_order": [],
                "evidence_sentences": sent_texts[:6],
                "expanded_source_sentences": expanded,
                "verifications": verification,
                "counts": {"first": len(first), "second": len(second), "third": 0},
                "gold_coverage": {"first_second": 0.0, "first_second_third": 0.0},
                "third_order_informative_ratio": 0.0,
                "answer": answer,
                "correct": is_hit,
            }
        )
        print(f"[{question[:40]}] mask={mask:.2f} 命中={is_hit} 原文句={len(expanded)}")

    save_json(OUT / "question_set_results.json", rows)
    save_json(OUT / "question_set_results_v4.json", rows)
    full = [r for r in rows if r["mask"] >= 0.99]
    acc = sum(1 for r in full if r["correct"]) / max(len(full), 1)
    print(f"\n全文本题（{len(full)} 题）v4 命中率: {acc:.0%}")


if __name__ == "__main__":
    main()
