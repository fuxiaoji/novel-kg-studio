"""C14: local-Qwen option evidence logits with adaptive second-round graph calls."""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from analyze_dense_retrieval import chunk_embeddings, embed  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c13_option_rebuttal import _option_packet  # noqa: E402
from c8_graph_passage import C8Context, LETTERS  # noqa: E402
from qwen_choice_logprob import QwenChoiceLogprobClient  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, graph_path  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
VERSION = "c14-local-qwen-adaptive-graph-evidence-logprob-v1"


def classify(
    client: QwenChoiceLogprobClient,
    q: dict[str, Any],
    letter: str,
    option: str,
    passages: list[dict[str, Any]],
    links: list[dict[str, Any]],
    stage: str,
) -> dict[str, Any]:
    text = "\n\n".join(f"[{row['id']}]\n{row['text']}" for row in passages)
    graph = "\n".join(
        f"- {row['source']} --{row['relation']}--> {row['target']}; source evidence: {row.get('evidence','')}"
        for row in links
    )
    prompt = (
        f"QUESTION\n{q['question']}\n\nCANDIDATE CLAIM\n{letter}. {option}\n\nSOURCE PASSAGES\n{text}"
        + (f"\n\nGRAPH RELATIONS WITH SOURCE EVIDENCE\n{graph}" if graph else "")
        + "\n\nClassify whether these sources establish this candidate as the answer to the question. "
        "A = directly supports; B = directly contradicts by giving a different answer; C = insufficient or ambiguous. "
        "Missing evidence is C, never B. Resolve translated aliases from event semantics. For NOT/EXCEPT/incorrect "
        "questions, A means the candidate satisfies the requested negative condition. Output exactly one ASCII letter: A, B, or C."
    )
    result = client.classify(
        "You are a conservative evidence classifier. Do not solve from memory; classify only the supplied source evidence.",
        prompt,
    )
    result.update({"stage": stage, "chunk_ids": [row["id"] for row in passages], "links": links})
    return result


def evidence_score(rounds: list[dict[str, Any]]) -> float:
    support = max(row["probabilities"]["A"] for row in rounds)
    contradict = max(row["probabilities"]["B"] for row in rounds)
    unknown = min(row["probabilities"]["C"] for row in rounds)
    return math.log(support + 0.03) - math.log(contradict + 0.25 * unknown + 0.03)


def answer_one(client: QwenChoiceLogprobClient, q: dict[str, Any], ctx: C8Context, matrix: Any) -> dict[str, Any]:
    queries = [f"Question: {q['question']}\nCandidate answer: {choice}" for choice in q["choices"][:4]]
    qmatrix = embed(queries)
    packets = [_option_packet(ctx, matrix, qmatrix[index], q, index) for index in range(4)]
    states = []
    for packet in packets:
        first = classify(client, q, packet["letter"], packet["option"], packet["chunks"][:2], [], "seed")
        states.append({"letter": packet["letter"], "option": packet["option"], "rounds": [first], "packet": packet})
    initial = sorted(states, key=lambda state: evidence_score(state["rounds"]), reverse=True)
    # Spend the extra graph budget only on the two candidates still most plausible.
    for state in initial[:2]:
        packet = state["packet"]
        follow = classify(client, q, packet["letter"], packet["option"], packet["chunks"][2:5], packet["links"][:3], "graph_followup")
        state["rounds"].append(follow)
    for state in states:
        state["score"] = evidence_score(state["rounds"])
        state.pop("packet", None)
    ranked = sorted(states, key=lambda state: state["score"], reverse=True)
    return {
        "method": "c14_adaptive_graph_evidence_logprob",
        "selected_letter": ranked[0]["letter"],
        "states": states,
        "score_margin": ranked[0]["score"] - ranked[1]["score"],
        "prompt_version": VERSION,
        "mask": "unmasked",
        "answer_model": client.model,
        "external_api": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen2.5:7b-32k")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--out", type=Path, default=BASE / "dqa_qwen_c14_20")
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    client = QwenChoiceLogprobClient(args.model)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(json.dumps({"version": VERSION, "model": args.model, "mask": "unmasked", "external_api": False, "novels": args.novels}, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(cases[n]["questions"]) for n in args.novels)
    completed = 0
    lock = threading.Lock()
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        jobs = []
        for qi, q in enumerate(case["questions"]):
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            if path.exists():
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                    if row.get("prompt_version") == VERSION and row.get("selected_letter") in LETTERS:
                        completed += 1
                        continue
                except Exception:
                    pass
            jobs.append((qi, q, path))

        def one(job: tuple[int, dict[str, Any], Path]) -> dict[str, Any]:
            qi, q, path = job
            row = answer_one(client, q, ctx, matrix)
            gold = LETTERS[q["gold_index"]]
            row.update({"novel": novel, "batch": "first10" if novel in FIRST10 else "second10", "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": gold, "correct": row["selected_letter"] == gold})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
            return row

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for future in as_completed(futures):
                row = future.result()
                with lock:
                    completed += 1
                    print(f"[{completed}/{total}] c14/{novel}/q{row['qi']} -> {row['selected_letter']} ({row['score_margin']:.3f})", flush=True)
    selected = set(args.novels)
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in (args.out / "answers").glob("*/*.json") if path.parent.name in selected]
    correct = sum(bool(row["correct"]) for row in rows)
    print(json.dumps({"correct": correct, "total": len(rows), "accuracy": correct / len(rows)}, indent=1))


if __name__ == "__main__":
    main()
