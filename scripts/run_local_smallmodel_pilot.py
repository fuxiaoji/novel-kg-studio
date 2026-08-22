"""Matched local-small-model baselines and C15 safe graph overlay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from analyze_dense_retrieval import chunk_embeddings, embed  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c13_option_rebuttal import _option_packet  # noqa: E402
from c8_graph_passage import C8Context, LETTERS, normalize_letter, retrieve_bm25  # noqa: E402
from native_ollama_client import NativeOllamaNoThinkClient  # noqa: E402
from run_c8_20 import NOVELS, graph_path  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
VERSION = "c15-qwen35-9b-nothink-safe-graph-overlay-v1"
TAIL_CHARS = 50_000


def option_text_packet(
    ctx: C8Context, matrix: Any, query_vector: Any, q: dict[str, Any], option_index: int
) -> dict[str, Any]:
    """Retrieve option-conditioned passages without any cloud client dependency."""
    option = q["choices"][option_index]
    local_q = {"question": q["question"], "choices": [option]}
    bm25 = [int(x) for x in retrieve_bm25(ctx, local_q, limit=36)["diagnostics"]["selected"]]
    dense_scores = matrix @ query_vector
    dense = list(map(int, np.argsort(dense_scores)[::-1][:80]))
    bm_rank = {index: rank for rank, index in enumerate(bm25)}
    dense_rank = {index: rank for rank, index in enumerate(dense)}
    candidates = set(bm25) | set(dense)
    rrf = {
        index: 1.0 / (40 + bm_rank.get(index, 10**6)) + 1.2 / (40 + dense_rank.get(index, 10**6))
        for index in candidates
    }
    selected: list[int] = []
    for index in sorted(candidates, key=lambda item: rrf[item], reverse=True):
        if any(abs(index - old) <= 1 for old in selected):
            continue
        selected.append(index)
        if len(selected) >= 5:
            break
    chunks = [
        {
            "id": ctx.base.chunks[index].id,
            "index": index,
            "start": ctx.base.chunks[index].start,
            "end": ctx.base.chunks[index].end,
            "text": ctx.base.chunks[index].text,
            "rrf_score": rrf[index],
        }
        for index in selected
    ]
    return {"letter": LETTERS[option_index], "option": option, "chunks": chunks, "links": []}


def options(q: dict[str, Any]) -> str:
    return "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(q["choices"][:4]))


def select(raw: Any) -> str | None:
    if isinstance(raw, dict):
        return normalize_letter(raw.get("selected_letter") or raw.get("answer") or raw.get("raw"))
    return normalize_letter(raw)


def answer_question(client: NativeOllamaNoThinkClient, q: dict[str, Any]) -> dict[str, Any]:
    raw = client.complete_json(
        "Answer the multiple-choice question. Output JSON only.",
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options(q)}\n\n"
        'Return only {"selected_letter":"A|B|C|D"}.',
        max_tokens=80,
    )
    return {"selected_letter": select(raw), "raw": raw}


def answer_tail(client: NativeOllamaNoThinkClient, q: dict[str, Any], text: str) -> dict[str, Any]:
    raw = client.complete_json(
        "Use only the supplied tail of the detective novel. Output JSON only.",
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options(q)}\n\nNOVEL TAIL\n{text[-TAIL_CHARS:]}\n\n"
        'Obey NOT/EXCEPT wording. Return only {"selected_letter":"A|B|C|D"}.',
        max_tokens=100,
    )
    return {"selected_letter": select(raw), "raw": raw, "tail_chars": TAIL_CHARS}


def answer_graph(client: NativeOllamaNoThinkClient, q: dict[str, Any], ctx: C8Context, matrix: Any) -> dict[str, Any]:
    queries = [f"Question: {q['question']}\nCandidate answer: {choice}" for choice in q["choices"][:4]]
    qmatrix = embed(queries)
    text_packets = [option_text_packet(ctx, matrix, qmatrix[index], q, index) for index in range(4)]
    graph_packets = [_option_packet(ctx, matrix, qmatrix[index], q, index) for index in range(4)]
    chunks: dict[str, dict[str, Any]] = {}
    for packet in text_packets:
        for rank, row in enumerate(packet["chunks"][:3]):
            item = chunks.setdefault(row["id"], {**row, "for_options": [], "best_rank": rank})
            item["for_options"].append(packet["letter"])
            item["best_rank"] = min(item["best_rank"], rank)
    ordered = sorted(chunks.values(), key=lambda row: (row["best_rank"], row["start"]))
    passages = "\n\n".join(
        f"[{row['id']} | candidates {','.join(sorted(set(row['for_options'])))}]\n{row['text']}" for row in ordered
    )
    links = []
    for packet in graph_packets:
        for link in packet["links"][:2]:
            links.append(
                f"- candidate {packet['letter']}: {link['source']} --{link['relation']}--> {link['target']}; "
                f"source evidence: {str(link.get('evidence',''))[:320]}"
            )
    prompt = (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options(q)}\n\nOPTION-CONDITIONED ORIGINAL PASSAGES\n{passages}\n\n"
        f"HIGH-VALUE GRAPH RELATIONS (supplement only; original passages have priority)\n" + "\n".join(links)
        + "\n\nEvaluate every option as a claim. Seek an explicit different identity, number, place, time, action, or motive to rebut it. "
        "Missing evidence is unknown, not refutation. Prefer final revelations over investigator hypotheses and red herrings. "
        "Resolve translated aliases by event semantics and obey NOT/EXCEPT wording. Return strict JSON only: "
        '{"selected_letter":"A|B|C|D","decisive_evidence":"brief quote or relation"}'
    )
    raw = client.complete_json(
        "You are a conservative detective-novel reader using compact option-conditioned text and grounded graph evidence.",
        prompt,
        max_tokens=220,
    )
    return {
        "selected_letter": select(raw),
        "raw": raw,
        "retrieval": {"chunks": ordered, "graph_links": links},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--policies", nargs="+", choices=("question_only", "tail", "graph"), default=("question_only", "tail", "graph"))
    parser.add_argument("--out", type=Path, default=BASE / "dqa_qwen35_c15_20")
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    client = NativeOllamaNoThinkClient(args.model)
    args.out.mkdir(parents=True, exist_ok=True)
    for novel in args.novels:
        case = cases[novel]
        ctx = matrix = None
        if "graph" in args.policies:
            graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
            ctx = C8Context.build(graph, case["text"], None)
            matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        for policy in args.policies:
            for qi, q in enumerate(case["questions"]):
                path = args.out / "answers" / policy / novel / f"q{qi:02d}.json"
                if path.exists():
                    try:
                        old = json.loads(path.read_text(encoding="utf-8"))
                        if old.get("version") == VERSION and old.get("selected_letter") in LETTERS:
                            continue
                    except Exception:
                        pass
                if policy == "question_only":
                    result = answer_question(client, q)
                elif policy == "tail":
                    result = answer_tail(client, q, case["text"])
                else:
                    result = answer_graph(client, q, ctx, matrix)
                gold = LETTERS[q["gold_index"]]
                result.update({"version": VERSION, "model": args.model, "thinking": "disabled", "external_api": False, "mask": "unmasked", "policy": policy, "novel": novel, "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": gold, "correct": result["selected_letter"] == gold})
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"[{policy}/{novel}/q{qi}] {result['selected_letter']} gold={gold}", flush=True)
    for policy in args.policies:
        rows = [json.loads(path.read_text(encoding="utf-8")) for novel in args.novels for path in (args.out / "answers" / policy / novel).glob("*.json")]
        print(policy, sum(bool(row["correct"]) for row in rows), "/", len(rows), flush=True)


if __name__ == "__main__":
    main()
