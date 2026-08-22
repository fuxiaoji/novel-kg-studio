"""MuSR adapter for Novel KG Studio.

Reuses the DetectiveQA pipeline (pass1 filter -> pass2 graph -> merge -> coref ->
consolidate -> GraphStore -> LLM retrieval) but changes the I/O contract:
  * answer format: official MuSR expects a final line  ``ANSWER: N``  (1-based).
  * no answer-paragraph masking: MuSR has no clue_position; mask concept N/A.
  * gold: choices + answer index (+ optional reasoning-tree leaves for coverage).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from eval_four_datasets import (  # noqa: E402
    OUT,
    build_case_graph,
    load_cases,
    make_client,
    options_block,
)
from novel_kg_studio.cache import load_json, save_json  # noqa: E402
from novel_kg_studio.store import GraphStore  # noqa: E402
from novel_kg_studio.store.bm25 import BM25Index  # noqa: E402
from novel_kg_studio.store.llm_retriever import execute, plan_search, top_sentences  # noqa: E402

MUSR_OUT = ROOT / "outputs" / "four_datasets" / "musr"


def extract_answer_index(answer: str, n_choices: int) -> int | None:
    """Official MuSR style: last ``ANSWER: N`` where N is 1-based."""
    matches = list(re.finditer(r"(?i)\bANSWER\s*[:=]?\s*(\d+)", answer))
    if matches:
        n = int(matches[-1].group(1))
        if 1 <= n <= n_choices:
            return n - 1
        return None
    # letter fallback
    m = re.search(r"\b([A-D])\b\s*[).:]?\s*$", answer.strip(), re.M)
    if m:
        idx = ord(m.group(1)) - ord("A")
        if idx < n_choices:
            return idx
    return None


def answer_full_musr(client: Any, q: dict, text: str, out_dir: Path, key: str) -> str:
    path = out_dir / f"full_{key}.json"
    cached = load_json(path)
    if cached:
        return cached["answer"]
    opt = options_block(q)
    prompt = (
        f"Question: {q['question']}\n\n{opt}\n\nNarrative:\n{text}\n\n"
        "Reason step by step using ONLY the narrative, then end your reply with exactly "
        "a line `ANSWER: N` where N is the number of your chosen option (1-based)."
    )
    raw = ""
    error = ""
    for _ in range(3):
        try:
            raw = client.complete(
                "You are a careful reasoning judge solving a MuSR-style story question.", prompt, max_tokens=4000
            )
            if raw.strip():
                break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    save_json(path, {"answer": raw, "error": error})
    return raw


def answer_graph_musr(client: Any, q: dict, store: GraphStore, kept: list[dict], out_dir: Path, key: str) -> str:
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in store.nodes)).encode("utf-8")).hexdigest()[:8]
    path = out_dir / f"graph_{key}_{graph_fp}.json"
    cached = load_json(path)
    if cached:
        return cached["answer"]
    plan = plan_search(client, q["question"], store, cache_dir=out_dir / "plans", graph_fp=graph_fp)
    first, second = execute(store, plan)
    sentence_index = BM25Index([row["text"] for row in kept])
    sent_hits = top_sentences(sentence_index, q["question"], plan, k=8)
    sent_texts = [kept[i]["text"] for i, _, _ in sent_hits]
    clue_lines = []
    for node_id in list(first[:8]) + list(second[:10]):
        node = store.by_id[node_id]
        clue_lines.append(
            f"- {node['name']} [{node['type']}]: {node.get('description', '')} | "
            f"{(node.get('evidence') or [''])[0][:140]}"
        )
    opt = options_block(q)
    prompt = (
        f"Question: {q['question']}\n\n{opt}\n\nGraph clues (entities, locations, objects, events, "
        f"with evidence):\n" + "\n".join(clue_lines)
        + "\n\nEvidence sentences:\n" + "\n".join(f"- {t}" for t in sent_texts[:8])
        + "\n\nReason step by step from the clues (mind decoys / misdirection), then end your reply "
        "with exactly a line `ANSWER: N` where N is the number of your chosen option (1-based)."
    )
    raw = ""
    error = ""
    for _ in range(3):
        try:
            raw = client.complete(
                "You are a careful reasoning judge answering from the provided graph clues only.", prompt, max_tokens=4000
            )
            if raw.strip():
                break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    save_json(path, {"answer": raw, "error": error})
    return raw


def judge_musr(answer: str, q: dict, client: Any, out_dir: Path, key: str) -> tuple[bool, str]:
    choices = q.get("choices") or []
    gold = q.get("gold_index")
    if gold is None or not choices:
        return False, "no gold"
    idx = extract_answer_index(answer, len(choices))
    if idx is not None:
        return idx == gold, f"parsed index {idx + 1}"
    # LLM judge fallback
    path = out_dir / f"j_{key}.json"
    cached = load_json(path)
    if cached:
        return bool(cached["correct"]), str(cached.get("note", ""))
    payload = client.complete_json(
        "You are a strict but fair answer judge for MuSR.",
        (
            f"Question: {q['question']}\nChoices: {json.dumps(choices, ensure_ascii=False)}\n"
            f"Gold answer (index 0-based): {gold} -> {choices[gold]}\nModel answer: {answer}\n"
            'Return strict JSON only: {"correct": true/false, "note": "one short reason"}'
        ),
    )
    verdict = {
        "correct": bool(payload.get("correct")) if isinstance(payload, dict) else False,
        "note": str(payload.get("note") or "") if isinstance(payload, dict) else "",
    }
    save_json(path, verdict)
    return verdict["correct"], verdict["note"]


def gold_coverage(store: GraphStore, kept: list[dict], q: dict) -> dict:
    """Cheap lexical coverage of explicit reasoning-tree leaves by graph evidence + kept text."""
    trees = (q.get("meta") or {}).get("reasoning_trees") or []
    evidence_texts = []
    for n in store.nodes:
        evidence_texts.append(f"{n['name']} {n.get('description', '')} " + " ".join(n.get("evidence") or []))
    kept_text = " ".join(row["text"] for row in kept)
    explicit = [
        leaf["text"]
        for tree in trees
        for leaf in tree.get("leaves", [])
        if leaf.get("fact_type") == "explicit" and leaf.get("text")
    ]
    hits = 0
    for text in explicit:
        words = [w.lower() for w in re.findall(r"[a-zA-Z']+", text) if len(w) > 3]
        if not words:
            continue
        if all(w in kept_text.lower() for w in words):
            hits += 1
    return {"explicit_leaves": len(explicit), "lexical_hits_in_kept": hits, "trees": len(trees)}


def run_case_musr(
    client: Any,
    build_client: Any,
    case: dict,
    cfg: dict,
    *,
    workers: int,
    resume: bool,
    build_graphs: bool,
) -> dict:
    case_id = case["case_id"]
    out_dir = MUSR_OUT / case_id
    graph = None
    if build_graphs:
        try:
            graph = build_case_graph(build_client, case, out_dir, cfg, workers=workers, resume=resume)
        except Exception as exc:
            print(f"[warn] graph build failed {case_id}: {type(exc).__name__}: {exc}")
    elif (out_dir / "graph.json").exists():
        graph = json.loads((out_dir / "graph.json").read_text(encoding="utf-8"))
    store = GraphStore(graph["nodes"], graph["edges"]) if graph else None
    kept = []
    kept_path = out_dir / "pass1" / "kept.jsonl"
    if kept_path.exists():
        kept = [json.loads(l) for l in kept_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = []
    for qi, q in enumerate(case["questions"]):
        key = hashlib.sha1(f"{case_id}|{qi}|{getattr(client, 'model', '?')}".encode("utf-8")).hexdigest()[:10]
        ans_full = answer_full_musr(client, q, case["text"], out_dir, key)
        correct, note = judge_musr(ans_full, q, client, out_dir, f"full_{key}")
        row = {
            "qid": q["qid"],
            "question": q["question"],
            "gold_index": q.get("gold_index"),
            "gold_text": q.get("gold_text"),
            "n_choices": len(q.get("choices") or []),
            "full_text": {"answer": ans_full, "correct": correct, "note": note},
        }
        if store is not None:
            ans_graph = answer_graph_musr(client, q, store, kept, out_dir, key)
            g_correct, g_note = judge_musr(ans_graph, q, client, out_dir, f"graph_{key}")
            row["graph"] = {"answer": ans_graph, "correct": g_correct, "note": g_note}
            row["coverage"] = gold_coverage(store, kept, q)
        rows.append(row)
        print(
            f"[{case_id}] full={'OK' if correct else 'XX'} graph="
            f"{'OK' if ('graph' in row and row['graph']['correct']) else 'XX'} "
            f"| {q['question'][:40]}"
        )
    return {"case_id": case_id, "dataset": "musr", "rows": rows}


def summarize(results: list[dict]) -> dict:
    by_domain: dict[str, dict] = {}
    for r in results:
        domain = r["case_id"].split("_")[1]
        d = by_domain.setdefault(domain, {"full_c": 0, "full_t": 0, "graph_c": 0, "graph_t": 0, "parse_c": 0})
        for row in r["rows"]:
            d["full_t"] += 1
            d["full_c"] += 1 if row["full_text"]["correct"] else 0
            if "graph" in row:
                d["graph_t"] += 1
                d["graph_c"] += 1 if row["graph"]["correct"] else 0
            if extract_answer_index(row["full_text"]["answer"], max(row.get("n_choices", 2), 2)) is not None:
                d["parse_c"] += 1
    return by_domain


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=6, help="cases per domain")
    parser.add_argument("--domains", nargs="+", default=["murder_mystery", "object_placements", "team_allocation"])
    parser.add_argument("--backend", default="deepseek", choices=["deepseek", "ollama", "urllib"])
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--build-model", default="deepseek-chat")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--case-workers", type=int, default=1, help="parallelism across cases")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--skip-graph", action="store_true")
    args = parser.parse_args()

    cfg = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    cfg["model"] = dict(cfg.get("model") or {})
    cfg["model"]["max_tokens_pass1"] = 3000
    cfg["model"]["max_tokens_pass2"] = 4000
    client = make_client(args.backend, args.model, "low", None)
    build_client = None if args.skip_graph else make_client("deepseek", args.build_model, None, None)

    cases = [c for c in load_cases("musr") if c["meta"].get("domain") in args.domains]
    results = []
    for domain in args.domains:
        picked = [c for c in cases if c["meta"].get("domain") == domain][: args.sample]
        print(f"== {domain}: {len(picked)} cases ==")
        if args.case_workers > 1:
            with ThreadPoolExecutor(max_workers=args.case_workers) as pool:
                futures = {
                    pool.submit(
                        run_case_musr,
                        client,
                        build_client,
                        case,
                        cfg,
                        workers=args.workers,
                        resume=not args.no_resume,
                        build_graphs=not args.skip_graph,
                    ): case["case_id"]
                    for case in picked
                }
                for fut in as_completed(futures):
                    results.append(fut.result())
        else:
            for case in picked:
                results.append(
                    run_case_musr(
                        client,
                        build_client,
                        case,
                        cfg,
                        workers=args.workers,
                        resume=not args.no_resume,
                        build_graphs=not args.skip_graph,
                    )
                )
    summ = summarize(results)
    for domain, d in summ.items():
        print(
            f"{domain}: full={d['full_c']}/{d['full_t']} ({d['full_c']/max(d['full_t'],1):.0%}) "
            f"graph={d['graph_c']}/{d['graph_t']} ({d['graph_c']/max(d['graph_t'],1):.0%})"
        )
    save_json(MUSR_OUT / "eval_results.json", {"summaries": summ, "results": results})


if __name__ == "__main__":
    main()
