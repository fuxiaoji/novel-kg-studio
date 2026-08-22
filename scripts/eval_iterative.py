"""Small-scale DetectiveQA eval with the group-style iterative verifier, vs one-shot graph and full-text."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.store import GraphStore
from novel_kg_studio.store.bm25 import BM25Index
from novel_kg_studio.store.iterative import run_iterative
from novel_kg_studio.store.llm_retriever import execute, plan_search, top_sentences
from novel_kg_studio.store.suspects import is_who_question, suspect_chain
from eval_multi import judge, official_questions

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def node_lines(store: GraphStore, first: list[str]) -> list[str]:
    lines = []
    for node_id in first[:6]:
        node = store.by_id[node_id]
        lines.append(f"{node['name']} [{node['type']}]: {node.get('description','')} {' | '.join(node.get('evidence', [])[:2])}".strip())
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=["103", "104", "117"])
    parser.add_argument("--model", default="deepseek-chat")
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    client = LLMClient(model=args.model, temperature=0.0, max_tokens=1200, retries=3)
    baseline = json.loads((OUT / "multi_results.json").read_text(encoding="utf-8"))
    graph_by_q = {}
    for result in baseline:
        for row in result["rows"]:
            graph_by_q[row["question"]] = row

    all_rows = []
    for novel_id in args.novels:
        cfg = (config.get("novels") or {}).get(novel_id)
        out_dir = _resolve(ROOT, cfg["output_dir"])
        graph = json.loads((out_dir / "graph.json").read_text(encoding="utf-8"))
        kept = [
            json.loads(line)
            for line in (out_dir / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        store = GraphStore(graph["nodes"], graph["edges"])
        sentence_index = BM25Index([r["text"] for r in kept])
        graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in graph["nodes"])).encode("utf-8")).hexdigest()[:8]
        cache_dir = out_dir / "iter_eval"
        questions = official_questions(_resolve(ROOT, cfg["anno_path"]))
        for q in questions:
            who = is_who_question(q["question"])
            plan = plan_search(client, q["question"], store, cache_dir=out_dir / "plans", graph_fp=graph_fp)
            first, second = execute(store, plan)
            sent_hits = top_sentences(sentence_index, q["question"], plan, k=6)
            sent_texts = [kept[i]["text"] for i, _, _ in sent_hits]
            clues = node_lines(store, first)
            key = hashlib.sha1(f"{novel_id}|{q['question']}".encode("utf-8")).hexdigest()[:12]

            candidates = []
            evidence_by_candidate = {}
            if who:
                suspects = suspect_chain(store, q["question"])[:3]
                for suspect in suspects:
                    name = suspect["name"]
                    candidates.append({"name": name})
                    ev = [e["evidence"] for e in suspect["edges"]]
                    node_id = next((n["id"] for n in store.nodes if n["name"] == name), None)
                    if node_id is not None:
                        for seq in store.by_id[node_id].get("source_sentence_ids", [])[:3]:
                            if int(seq) < len(kept):
                                ev.append(kept[int(seq)]["text"])
                    evidence_by_candidate[name] = sent_texts[:3] + ev[:6]
            else:
                for choice in q["choices"]:
                    name = str(choice).strip()
                    candidates.append({"name": name})
                    hits = top_sentences(sentence_index, f"{q['question']} {name}", plan, k=3)
                    ev = [kept[i]["text"] for i, _, _ in hits]
                    node_id = next((n["id"] for n in store.nodes if n["name"].lower() == name.lower()), None)
                    if node_id is not None:
                        node = store.by_id[node_id]
                        ev.extend(node.get("evidence", []))
                        for seq in node.get("source_sentence_ids", [])[:2]:
                            if int(seq) < len(kept):
                                ev.append(kept[int(seq)]["text"])
                    evidence_by_candidate[name] = (ev or sent_texts[:3])[:8]

            results = run_iterative(
                client,
                question=q["question"],
                candidates=candidates,
                evidence_by_candidate=evidence_by_candidate,
                clues=clues,
                cache_dir=cache_dir,
                key=f"v2_{key}",
                max_obligations=2 if who else 1,
                who=who,
            )
            winner = results[0]["candidate"] if results else ""
            correct, note = judge(client, f"iter_{key}", q["question"], q["gold"], winner, cache_dir)
            graph_row = graph_by_q.get(q["question"])
            all_rows.append(
                {
                    "novel": novel_id,
                    "question": q["question"],
                    "gold": q["gold"],
                    "iterative": {"winner": winner, "correct": correct, "note": note, "states": [(r["candidate"], r["state"], r["score"]) for r in results]},
                    "graph_one_shot": {"correct": graph_row["graph"]["correct"] if graph_row else False},
                    "full_text": {"correct": graph_row["full_text"]["correct"] if graph_row else False},
                }
            )
            print(
                f"[{novel_id}] {q['question'][:40]} | iter={'对' if correct else '错'}({winner[:24]}) "
                f"graph={'对' if graph_row and graph_row['graph']['correct'] else '错'} "
                f"full={'对' if graph_row and graph_row['full_text']['correct'] else '错'}"
            )
    save_json(OUT / "iterative_results.json", all_rows)
    totals = {"iter": 0, "graph": 0, "full": 0}
    for row in all_rows:
        totals["iter"] += int(row["iterative"]["correct"])
        totals["graph"] += int(row["graph_one_shot"]["correct"])
        totals["full"] += int(row["full_text"]["correct"])
    n = len(all_rows)
    print(f"\n== 三本小说官方题（小规模，{n} 题）==")
    print(f"  iterative 迭代验证: {totals['iter']}/{n}")
    print(f"  graph 一次性: {totals['graph']}/{n}")
    print(f"  full_text 基线: {totals['full']}/{n}")


if __name__ == "__main__":
    main()
