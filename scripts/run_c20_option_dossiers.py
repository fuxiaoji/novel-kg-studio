"""C20: compact per-option evidence dossiers with graph navigation hints."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from analyze_dense_retrieval import chunk_embeddings, embed  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c13_option_rebuttal import _option_packet  # noqa: E402
from c8_graph_passage import C8Context, LETTERS, normalize_letter  # noqa: E402
from native_ollama_client import NativeOllamaNoThinkClient  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, graph_path  # noqa: E402
from run_local_smallmodel_pilot import option_text_packet  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
VERSION = "c20-local-separated-option-dossiers-v1"


def options_text(q: dict[str, Any]) -> str:
    return "\n".join(f"{LETTERS[index]}. {choice}" for index, choice in enumerate(q["choices"][:4]))


def answer(client: NativeOllamaNoThinkClient, q: dict[str, Any], ctx: C8Context, matrix: Any) -> dict[str, Any]:
    queries = [f"Question: {q['question']}\nCandidate answer: {choice}" for choice in q["choices"][:4]]
    qmatrix = embed(queries)
    dossiers = []
    trace = []
    for index, letter in enumerate(LETTERS):
        text_packet = option_text_packet(ctx, matrix, qmatrix[index], q, index)
        graph_packet = _option_packet(ctx, matrix, qmatrix[index], q, index)
        passages = "\n\n".join(f"[{row['id']}]\n{row['text']}" for row in text_packet["chunks"][:3])
        # Graph links are navigation hints only.  Keep at most one to prevent
        # repeated protagonist/witness edges from overwhelming source text.
        link = graph_packet["links"][:1]
        hint = ""
        if link:
            row = link[0]
            hint = f"\nGraph hint: {row['source']} --{row['relation']}--> {row['target']}; source evidence: {row.get('evidence','')}"
        dossiers.append(f"=== CANDIDATE {letter}: {q['choices'][index]} ===\n{passages}{hint}")
        trace.append({"letter": letter, "chunks": text_packet["chunks"][:3], "graph_hint": link})
    prompt = (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options_text(q)}\n\nPER-CANDIDATE ORIGINAL-TEXT DOSSIERS\n"
        + "\n\n".join(dossiers)
        + "\n\nAudit every candidate separately. A dossier was retrieved by that candidate but may contain neutral or misleading "
        "mentions; retrieval is not support. Find explicit support or an explicit different identity, action, place, time, number, "
        "or motive that refutes the claim. Missing evidence is unknown. Prefer the final solution over hypotheses and red herrings. "
        "Graph hints never override original text. Resolve translated aliases by events and obey NOT/EXCEPT wording. Return JSON only: "
        '{"selected_letter":"A|B|C|D","supported_candidates":["A"],"refuted_candidates":["B"],"decisive_evidence":"brief"}'
    )
    raw = client.complete_json("Compare four candidate-specific novel evidence dossiers. No chain of thought.", prompt, max_tokens=220)
    return {"selected_letter": normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw), "raw": raw, "retrieval": trace}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--out", type=Path, default=BASE / "dqa_local_c20_dossiers20")
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    client = NativeOllamaNoThinkClient(args.model)
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        for qi, q in enumerate(case["questions"][: args.max_questions or None]):
            path = args.out / "answers" / args.model.replace(":", "_") / novel / f"q{qi:02d}.json"
            if path.exists():
                continue
            result = answer(client, q, ctx, matrix)
            gold = LETTERS[q["gold_index"]]
            result.update({"version": VERSION, "model": args.model, "thinking": "disabled", "external_api": False, "mask": "unmasked", "novel": novel, "batch": "first10" if novel in FIRST10 else "second10", "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": gold, "correct": result["selected_letter"] == gold})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[{args.model}/{novel}/q{qi}] {result['selected_letter']} gold={gold}", flush=True)


if __name__ == "__main__":
    main()
