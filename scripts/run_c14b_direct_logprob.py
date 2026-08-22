"""C14B: local-Qwen direct A-D logits with adaptive graph-focused second read."""

from __future__ import annotations

import argparse
import json
import math
import sys
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
VERSION = "c14b-local-qwen-direct-option-logprob-adaptive-graph-v1"


def evidence_prompt(q: dict[str, Any], packets: list[dict[str, Any]], focus: list[str] | None = None) -> str:
    chunks: dict[str, dict[str, Any]] = {}
    for packet in packets:
        limit = 5 if focus and packet["letter"] in focus else 3
        if focus and packet["letter"] not in focus:
            continue
        for rank, row in enumerate(packet["chunks"][:limit]):
            item = chunks.setdefault(row["id"], {**row, "for_options": [], "best_rank": rank})
            item["for_options"].append(packet["letter"])
            item["best_rank"] = min(item["best_rank"], rank)
    ordered = sorted(chunks.values(), key=lambda row: (row["best_rank"], row["start"]))
    passages = "\n\n".join(
        f"[{row['id']} | retrieved for {','.join(sorted(set(row['for_options'])))}]\n{row['text']}" for row in ordered
    )
    links = []
    for packet in packets:
        if focus and packet["letter"] not in focus:
            continue
        for link in packet["links"][:3]:
            links.append(
                f"- candidate {packet['letter']}: {link['source']} --{link['relation']}--> {link['target']}; "
                f"source evidence: {str(link.get('evidence',''))[:280]}"
            )
    options = "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(q["choices"][:4]))
    focus_note = f"\nThe first read narrowed the comparison to {', '.join(focus)}. Compare those candidates especially carefully." if focus else ""
    return (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options}\n\nOPTION-CONDITIONED SOURCE PASSAGES\n{passages}"
        + ("\n\nGRAPH RELATIONS WITH SOURCE EVIDENCE\n" + "\n".join(links) if links else "")
        + focus_note
        + "\n\nChoose the answer established by the source. Prefer explicit final revelations over investigator hypotheses and decoys. "
        "Check exact identity, number, place, time, action, and motive. Resolve translated aliases from event semantics. "
        "For NOT/EXCEPT/incorrect questions obey the negative wording. Output exactly one ASCII option letter: A, B, C, or D."
    )


def entropy(probs: dict[str, float]) -> float:
    return -sum(value * math.log(max(value, 1e-12)) for value in probs.values())


def answer_one(client: QwenChoiceLogprobClient, q: dict[str, Any], ctx: C8Context, matrix: Any) -> dict[str, Any]:
    queries = [f"Question: {q['question']}\nCandidate answer: {choice}" for choice in q["choices"][:4]]
    qmatrix = embed(queries)
    packets = [_option_packet(ctx, matrix, qmatrix[index], q, index) for index in range(4)]
    first = client.classify(
        "You are a careful detective-novel multiple-choice reader. Use only the supplied sources and output one letter.",
        evidence_prompt(q, packets),
        labels="ABCD",
    )
    ranking = sorted(LETTERS, key=lambda letter: first["probabilities"][letter], reverse=True)
    margin = first["probabilities"][ranking[0]] - first["probabilities"][ranking[1]]
    second = None
    selected = ranking[0]
    # A second, graph-focused read is only useful when the first distribution is not decisive.
    if first["probabilities"][ranking[0]] < 0.80 or margin < 0.45:
        second = client.classify(
            "You are resolving the two most plausible answers using additional graph-linked source evidence. Output one letter.",
            evidence_prompt(q, packets, ranking[:2]),
            labels="ABCD",
        )
        combined = {
            letter: math.log(first["probabilities"][letter] + 1e-8) + math.log(second["probabilities"][letter] + 1e-8)
            for letter in LETTERS
        }
        selected = max(LETTERS, key=lambda letter: combined[letter])
    return {
        "method": "c14b_direct_option_logprob_adaptive_graph",
        "selected_letter": selected,
        "first_read": first,
        "second_read": second,
        "first_ranking": ranking,
        "first_margin": margin,
        "first_entropy": entropy(first["probabilities"]),
        "prompt_version": VERSION,
        "mask": "unmasked",
        "answer_model": client.model,
        "external_api": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen2.5:7b-32k")
    parser.add_argument("--out", type=Path, default=BASE / "dqa_qwen_c14b_20")
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    client = QwenChoiceLogprobClient(args.model)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "manifest.json").write_text(json.dumps({"version": VERSION, "model": args.model, "mask": "unmasked", "external_api": False, "novels": args.novels}, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(cases[n]["questions"]) for n in args.novels)
    completed = 0
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
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
            row = answer_one(client, q, ctx, matrix)
            gold = LETTERS[q["gold_index"]]
            row.update({"novel": novel, "batch": "first10" if novel in FIRST10 else "second10", "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": gold, "correct": row["selected_letter"] == gold})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
            completed += 1
            print(f"[{completed}/{total}] c14b/{novel}/q{qi} -> {row['selected_letter']} ({row['first_margin']:.3f})", flush=True)
    selected_novels = set(args.novels)
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in (args.out / "answers").glob("*/*.json") if path.parent.name in selected_novels]
    correct = sum(bool(row["correct"]) for row in rows)
    print(json.dumps({"correct": correct, "total": len(rows), "accuracy": correct / len(rows)}, indent=1))


if __name__ == "__main__":
    main()
