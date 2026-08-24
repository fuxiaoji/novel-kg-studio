"""G7 pure-graph QA: graph-guided source expansion without baseline access."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from analyze_dense_retrieval import chunk_embeddings, embed  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c13_option_rebuttal import _option_packet  # noqa: E402
from c8_graph_passage import C8Context, LETTERS, normalize_letter  # noqa: E402
from native_ollama_client import NativeOllamaNoThinkClient  # noqa: E402
from novel_kg_studio.llm import extract_json  # noqa: E402
from run_dqa30_g6_graph_expansion import build_dossier, options_text  # noqa: E402

DEFAULT_NOVELS = ["93", "97", "99", "100", "103", "104", "105", "106", "107", "108"]


def strict_letter(value):
    return normalize_letter(value) or "?"


def parse_object(raw: str):
    try:
        parsed = extract_json(raw)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    match = re.search(r"(?:selected_letter|answer)\s*[:=\"']+\s*([ABCD])\b", raw, re.I)
    return {"selected_letter": match.group(1).upper(), "raw": raw} if match else {"raw": raw}


def complete_choice(client, system: str, user: str, max_tokens: int = 320):
    suffix = " Return exactly one JSON object and select exactly one of A, B, C, or D."
    first = client.complete(system + suffix, user, max_tokens=max_tokens)
    parsed = parse_object(first)
    if strict_letter(parsed.get("selected_letter")) in set(LETTERS):
        return parsed
    second = client.complete(system + suffix + " Never abstain; choose the closest supported option.", user, max_tokens=max_tokens)
    parsed = parse_object(second)
    parsed["forced_choice_after_abstention"] = True
    parsed["first_raw"] = first
    return parsed


def prompt(question, dossier):
    passages = "\n\n".join(
        f"[{row['id']} | position {int(row['start'])} | graph candidates {','.join(sorted(set(row['for_options'])))}]\n{row['text']}"
        for row in dossier["chunks"]
    )
    links = []
    for row in dossier["links"]:
        sign = "REFUTES" if row["relation"] == "contradicts" else "SUPPORT-HYPOTHESIS"
        links.append(
            f"{row['candidate']} {sign}: {row['source']} --{row['relation']}--> {row['target']} "
            f"[{row['chunk_id']}: {row['evidence'][:280]}]"
        )
    return (
        f"QUESTION\n{question['question']}\n\nOPTIONS\n{options_text(question)}\n\n"
        f"GRAPH-SELECTED ORIGINAL EVIDENCE\n{passages}\n\nGROUNDED GRAPH HYPOTHESES\n"
        + ("\n".join(links) or "No relation survived grounding checks.")
        + "\n\nUse only the graph-selected source evidence. Graph relations are hypotheses, not facts. "
        "For every option, identify its strongest supporting fact and strongest contradiction. Prefer explicit late revelations "
        "over early suspicions, and reject aliases not grounded in the quoted passage. For NOT/EXCEPT questions reverse the "
        "criterion carefully. Return JSON: "
        '{"selected_letter":"A|B|C|D","confidence":"high|medium|low","support_ids":["c_1"],"reason":"brief contrast"}'
    )


def valid_cache(path: Path, signature: str, graph_sha: str):
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return row.get("signature") == signature and row.get("graph_sha256") == graph_sha and row.get("selected_letter") in set(LETTERS)
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=DEFAULT_NOVELS)
    parser.add_argument("--graph-root", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--chunk-limit", type=int, default=8)
    parser.add_argument("--link-limit", type=int, default=10)
    args = parser.parse_args()

    args.out_root.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    client = NativeOllamaNoThinkClient(args.model, num_ctx=args.num_ctx)
    signature = hashlib.sha256(
        Path(__file__).read_bytes() + f"|{args.chunk_limit}|{args.link_limit}|{args.model}|{args.num_ctx}".encode()
    ).hexdigest()[:16]
    total = sum(len(cases[n]["questions"]) for n in args.novels)
    completed = 0
    started = time.time()

    for novel in args.novels:
        case = cases[novel]
        graph_path = args.graph_root / "novels" / novel / "graph.json"
        graph_sha = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        queries = [
            f"Question: {question['question']}\nCandidate answer: {choice}"
            for question in case["questions"] for choice in question["choices"][:4]
        ]
        query_matrix = embed(queries)
        for qi, question in enumerate(case["questions"]):
            answer_path = args.out_root / "answers" / novel / f"q{qi:02d}.json"
            if valid_cache(answer_path, signature, graph_sha):
                completed += 1
                continue
            vectors = query_matrix[qi * 4: qi * 4 + 4]
            dossier = build_dossier(ctx, matrix, vectors, question, chunk_limit=args.chunk_limit, link_limit=args.link_limit)
            raw = complete_choice(
                client,
                "You are a conservative detective-novel evidence judge. Do not use outside knowledge.",
                prompt(question, dossier),
            )
            letter = strict_letter(raw.get("selected_letter"))
            if letter not in set(LETTERS):
                raise RuntimeError(f"unparsed answer for {novel}/q{qi}: {raw}")
            output = {
                "version": "g7-pure-graph-source-expansion-v1", "signature": signature,
                "model": args.model, "thinking": "disabled", "baseline_access": False,
                "graph_sha256": graph_sha, "novel": novel, "qi": qi, "qid": question.get("qid"),
                "question": question["question"], "choices": question["choices"][:4],
                "gold_letter": question["answer_letter"], "selected_letter": letter,
                "correct": letter == question["answer_letter"], "confidence": raw.get("confidence", ""),
                "support_ids": raw.get("support_ids", []), "reason": raw.get("reason", ""),
                "retrieval": dossier, "raw": raw,
            }
            answer_path.parent.mkdir(parents=True, exist_ok=True)
            answer_path.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")
            completed += 1
            print(f"[{completed}/{total}] {novel}/q{qi} G7={letter} gold={question['answer_letter']} chunks={len(dossier['chunks'])} links={len(dossier['links'])}", flush=True)
        progress = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "completed": completed, "total": total, "elapsed_minutes": round((time.time() - started) / 60, 2)}
        (args.out_root / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
