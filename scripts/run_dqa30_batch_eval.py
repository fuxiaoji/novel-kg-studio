"""Evaluate one new DetectiveQA batch with five graph methods and three text baselines."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from analyze_dense_retrieval import chunk_embeddings, embed  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c13_option_rebuttal import _option_packet  # noqa: E402
from c8_graph_passage import C8Context, LETTERS, normalize_letter  # noqa: E402
from native_ollama_client import NativeOllamaNoThinkClient  # noqa: E402
from run_compress_20 import compress_novel_parallel  # noqa: E402
from run_local_smallmodel_pilot import option_text_packet  # noqa: E402

VERSION = "dqa30-attention-batch-eval-v1"
DEFAULT_NOVELS = ["93", "97", "99", "100", "103", "104", "105", "106", "107", "108"]
METHODS = ("G1", "G2", "G3", "G4", "G5", "B1", "B2", "B3", "Q0")
GRAPH_METHODS = ("G1", "G2", "G3", "G4", "G5")
BASELINES = ("B1", "B2", "B3")
PERMUTATIONS = {
    "G1": [0, 1, 2, 3],
    "G2": [3, 2, 1, 0],
    "G3": [1, 2, 3, 0],
}


def options_text(question: dict[str, Any]) -> str:
    return "\n".join(f"{LETTERS[index]}. {choice}" for index, choice in enumerate(question["choices"][:4]))


def select_letter(payload: Any) -> str | None:
    if isinstance(payload, dict):
        return normalize_letter(payload.get("selected_letter") or payload.get("answer") or payload.get("raw"))
    return normalize_letter(payload)


def call_json(
    client: NativeOllamaNoThinkClient,
    system: str,
    prompt: str,
    *,
    max_tokens: int,
) -> tuple[str | None, Any]:
    payload: Any = {}
    for attempt in range(4):
        try:
            payload = client.complete_json(system, prompt, max_tokens=max_tokens)
        except Exception:
            payload = {}
        letter = select_letter(payload)
        if letter is not None and letter in LETTERS:
            return letter, payload
        if attempt < 3:
            prompt = prompt + "\n\nYour previous response was invalid or empty. Return strict JSON only."
            time.sleep(2.0 * (attempt + 1))
    return None, payload


def evidence_package(
    question: dict[str, Any],
    ctx: C8Context,
    matrix: np.ndarray,
    option_vectors: np.ndarray,
    *,
    include_graph: bool,
) -> dict[str, Any]:
    text_packets = [
        option_text_packet(ctx, matrix, option_vectors[index], question, index)
        for index in range(4)
    ]
    graph_packets = (
        [_option_packet(ctx, matrix, option_vectors[index], question, index) for index in range(4)]
        if include_graph
        else []
    )
    chunks: dict[str, dict[str, Any]] = {}
    for packet in text_packets:
        for rank, row in enumerate(packet["chunks"][:3]):
            item = chunks.setdefault(
                row["id"],
                {**row, "for_options": [], "best_rank": rank},
            )
            item["for_options"].append(packet["letter"])
            item["best_rank"] = min(item["best_rank"], rank)
    ordered = sorted(chunks.values(), key=lambda row: (row["best_rank"], row["start"]))
    links: list[dict[str, Any]] = []
    for packet in graph_packets:
        for link in packet["links"][:2]:
            links.append({"candidate": packet["letter"], **link})
    return {"chunks": ordered, "links": links}


def evidence_prompt(question: dict[str, Any], package: dict[str, Any], *, include_graph: bool) -> str:
    passages = "\n\n".join(
        f"[{row['id']} | candidates {','.join(sorted(set(row['for_options'])))}]\n{row['text']}"
        for row in package["chunks"]
    )
    graph_section = ""
    if include_graph:
        graph_lines = [
            f"- candidate {row['candidate']}: {row['source']} --{row['relation']}--> "
            f"{row['target']}; source evidence: {str(row.get('evidence', ''))[:320]}"
            for row in package["links"]
        ]
        graph_section = (
            "\n\nHIGH-VALUE GRAPH RELATIONS (supplement only; original passages have priority)\n"
            + "\n".join(graph_lines)
        )
    return (
        f"QUESTION\n{question['question']}\n\nOPTIONS\n{options_text(question)}\n\n"
        f"OPTION-CONDITIONED ORIGINAL PASSAGES\n{passages}{graph_section}\n\n"
        "Evaluate every option as a claim. Retrieval is not proof. Prefer explicit final revelations over "
        "early hypotheses and red herrings. Missing evidence is unknown, not refutation. Resolve translated "
        "aliases by event semantics and obey NOT/EXCEPT wording. Return strict JSON only: "
        '{"selected_letter":"A|B|C|D","decisive_evidence":"brief quote or relation"}'
    )


def answer_evidence(
    client: NativeOllamaNoThinkClient,
    question: dict[str, Any],
    ctx: C8Context,
    matrix: np.ndarray,
    option_vectors: np.ndarray,
    *,
    include_graph: bool,
) -> dict[str, Any]:
    package = evidence_package(
        question,
        ctx,
        matrix,
        option_vectors,
        include_graph=include_graph,
    )
    letter, raw = call_json(
        client,
        (
            "You are a conservative detective-novel reader using compact option-conditioned text"
            + (" and grounded graph evidence." if include_graph else ".")
        ),
        evidence_prompt(question, package, include_graph=include_graph),
        max_tokens=220,
    )
    return {"selected_letter": letter, "raw": raw, "retrieval": package}


def permuted_question(question: dict[str, Any], order: list[int]) -> dict[str, Any]:
    return {**question, "choices": [question["choices"][index] for index in order]}


def map_to_original(letter: str | None, order: list[int]) -> str | None:
    if letter not in LETTERS:
        return None
    return LETTERS[order[LETTERS.index(letter)]]


def answer_tail(
    client: NativeOllamaNoThinkClient,
    question: dict[str, Any],
    novel_text: str,
) -> dict[str, Any]:
    tail = novel_text[-50_000:]
    letter, raw = call_json(
        client,
        "Use only the supplied tail of the detective novel. Output JSON only.",
        f"QUESTION\n{question['question']}\n\nOPTIONS\n{options_text(question)}\n\n"
        f"NOVEL TAIL\n{tail}\n\nObey NOT/EXCEPT wording. "
        'Return only {"selected_letter":"A|B|C|D"}.',
        max_tokens=120,
    )
    return {"selected_letter": letter, "raw": raw, "tail_chars": len(tail)}


def answer_question_only(
    client: NativeOllamaNoThinkClient,
    question: dict[str, Any],
) -> dict[str, Any]:
    letter, raw = call_json(
        client,
        "Answer the multiple-choice question without novel context. Output JSON only.",
        f"QUESTION\n{question['question']}\n\nOPTIONS\n{options_text(question)}\n\n"
        'Return only {"selected_letter":"A|B|C|D"}.',
        max_tokens=80,
    )
    return {"selected_letter": letter, "raw": raw}


def answer_compressed(
    client: NativeOllamaNoThinkClient,
    question: dict[str, Any],
    compressed: str,
) -> dict[str, Any]:
    letter, raw = call_json(
        client,
        "Use only the supplied compressed novel. Output JSON only.",
        f"QUESTION\n{question['question']}\n\nOPTIONS\n{options_text(question)}\n\n"
        f"COMPRESSED NOVEL\n{compressed}\n\nObey NOT/EXCEPT wording. "
        'Return only {"selected_letter":"A|B|C|D"}.',
        max_tokens=160,
    )
    return {"selected_letter": letter, "raw": raw, "summary_chars": len(compressed)}


def majority_or_tail(votes: list[str | None], tail: str | None) -> tuple[str | None, str]:
    counts = Counter(letter for letter in votes if letter in LETTERS)
    if counts:
        winner, count = counts.most_common(1)[0]
        if count >= 2:
            return winner, "graph_majority"
    return tail, "tail_fallback"


def valid_cache(path: Path, *, model: str, graph_sha256: str, source_hash: str) -> bool:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return (
            row.get("version") == VERSION
            and row.get("model") == model
            and row.get("graph_sha256") == graph_sha256
            and row.get("source_hash") == source_hash
            and all(row.get("answers", {}).get(method, {}).get("selected_letter") in LETTERS for method in METHODS)
        )
    except Exception:
        return False


def exact_mcnemar(rows: list[dict[str, Any]], method: str, baseline: str) -> dict[str, Any]:
    wins = sum(row["correct"][method] and not row["correct"][baseline] for row in rows)
    losses = sum(not row["correct"][method] and row["correct"][baseline] for row in rows)
    discordant = wins + losses
    if not discordant:
        p_value = 1.0
    else:
        lower = min(wins, losses)
        tail = sum(math.comb(discordant, index) for index in range(lower + 1)) / (2**discordant)
        p_value = min(1.0, 2 * tail)
    return {"wins": wins, "losses": losses, "exact_p": p_value}


def clustered_interval(
    rows: list[dict[str, Any]],
    method: str,
    baseline: str,
    *,
    samples: int = 4000,
    seed: int = 20260822,
) -> list[float]:
    by_novel: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_novel.setdefault(row["novel"], []).append(row)
    novels = sorted(by_novel)
    rng = random.Random(seed)
    deltas = []
    for _ in range(samples):
        sample = [row for _ in novels for row in by_novel[rng.choice(novels)]]
        delta = sum(row["correct"][method] for row in sample) / len(sample)
        delta -= sum(row["correct"][baseline] for row in sample) / len(sample)
        deltas.append(delta)
    deltas.sort()
    return [deltas[int(0.025 * samples)], deltas[int(0.975 * samples)]]


def analyze(root: Path, novels: list[str]) -> dict[str, Any]:
    rows = []
    for novel in novels:
        for path in sorted((root / "answers" / novel).glob("q*.json")):
            item = json.loads(path.read_text(encoding="utf-8"))
            gold = item["gold_letter"]
            selected = {method: item["answers"][method]["selected_letter"] for method in METHODS}
            rows.append(
                {
                    "novel": novel,
                    "qi": item["qi"],
                    "qid": item["qid"],
                    "gold": gold,
                    "selected": selected,
                    "correct": {method: selected[method] == gold for method in METHODS},
                }
            )
    if not rows:
        raise RuntimeError("no answer rows to analyze")
    hard = [row for row in rows if not row["correct"]["Q0"]]

    def score(subset: list[dict[str, Any]], method: str) -> dict[str, Any]:
        correct = sum(row["correct"][method] for row in subset)
        return {"correct": correct, "total": len(subset), "accuracy": correct / len(subset) if subset else 0.0}

    report = {
        "metadata": {
            "version": VERSION,
            "novels": novels,
            "questions": len(rows),
            "single_model": "qwen3.5:9b",
            "thinking": "disabled",
            "mask": "unmasked",
        },
        "all": {method: score(rows, method) for method in METHODS},
        "question_only_wrong": {method: score(hard, method) for method in METHODS if method != "Q0"},
        "per_novel": {
            novel: {method: score([row for row in rows if row["novel"] == novel], method) for method in METHODS}
            for novel in novels
        },
        "paired": {
            f"{method}_vs_{baseline}": {
                **exact_mcnemar(rows, method, baseline),
                "novel_cluster_delta_95": clustered_interval(rows, method, baseline),
            }
            for method in GRAPH_METHODS
            for baseline in BASELINES
        },
    }
    (root / "analysis.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    with (root / "per_question.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["novel", "qi", "qid", "gold", *METHODS]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "novel": row["novel"],
                    "qi": row["qi"],
                    "qid": row["qid"],
                    "gold": row["gold"],
                    **row["selected"],
                }
            )
    lines = [
        "# DetectiveQA 新10本：Qwen3.5 9B批次报告",
        "",
        "| 方法 | 正确/总数 | 准确率 |",
        "|---|---:|---:|",
    ]
    for method in METHODS:
        block = report["all"][method]
        lines.append(f"| {method} | {block['correct']}/{block['total']} | {block['accuracy']:.1%} |")
    lines += [
        "",
        f"仅题目+选项答错的困难子集：{len(hard)}/{len(rows)}题。",
        "",
        "G1/G2/G3分别为原序、逆序和循环序图谱方法；G4为原序与逆序一致门控，"
        "G5为三排列多数门控；B1尾窗口、B2全量压缩、B3普通BGE-M3+BM25 RRF。",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-root", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--novels", nargs="+", default=DEFAULT_NOVELS)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--summary-workers", type=int, default=1)
    args = parser.parse_args()

    args.out_root.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    client = NativeOllamaNoThinkClient(args.model, num_ctx=args.num_ctx)
    total = sum(len(cases[novel]["questions"]) for novel in args.novels)
    completed = 0
    started = time.time()
    manifest: dict[str, Any] = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "source_hash": source_hash,
        "model": args.model,
        "num_ctx": args.num_ctx,
        "novels": args.novels,
        "methods": METHODS,
        "graph_root": str(args.graph_root.resolve()),
        "thinking": "disabled",
        "mask": "unmasked",
    }

    (args.out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    for novel in args.novels:
        case = cases[novel]
        graph_path = args.graph_root / "novels" / novel / "graph.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        if not (graph.get("quality") or {}).get("passed"):
            raise RuntimeError(f"{novel}: graph lacks a passing quality report")
        graph_sha256 = hashlib.sha256(graph_path.read_bytes()).hexdigest()
        ctx = C8Context.build(graph, case["text"], None)

        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        flat_queries = [
            f"Question: {question['question']}\nCandidate answer: {choice}"
            for question in case["questions"]
            for choice in question["choices"][:4]
        ]
        flat_vectors = embed(flat_queries)
        query_vectors = [
            flat_vectors[index * 4 : index * 4 + 4]
            for index in range(len(case["questions"]))
        ]

        compression_dir = args.out_root / "compression" / novel
        compression_dir.mkdir(parents=True, exist_ok=True)
        compressed = compress_novel_parallel(
            client,
            case["text"],
            compression_dir,
            args.summary_workers,
        )
        if not compressed.strip():
            raise RuntimeError(f"{novel}: empty compressed baseline")

        for qi, question in enumerate(case["questions"]):
            answer_path = args.out_root / "answers" / novel / f"q{qi:02d}.json"
            if valid_cache(
                answer_path,
                model=args.model,
                graph_sha256=graph_sha256,
                source_hash=source_hash,
            ):
                completed += 1
                continue
            answers: dict[str, dict[str, Any]] = {}
            for method, order in PERMUTATIONS.items():
                displayed = permuted_question(question, order)
                vectors = query_vectors[qi][order]
                result = answer_evidence(
                    client,
                    displayed,
                    ctx,
                    matrix,
                    vectors,
                    include_graph=True,
                )
                result["displayed_letter"] = result["selected_letter"]
                result["selected_letter"] = map_to_original(result["selected_letter"], order)
                result["permutation"] = order
                answers[method] = result

            answers["B1"] = answer_tail(client, question, case["text"])
            answers["B2"] = answer_compressed(client, question, compressed)
            answers["B3"] = answer_evidence(
                client,
                question,
                ctx,
                matrix,
                query_vectors[qi],
                include_graph=False,
            )
            answers["Q0"] = answer_question_only(client, question)

            g1 = answers["G1"]["selected_letter"]
            g2 = answers["G2"]["selected_letter"]
            tail = answers["B1"]["selected_letter"]
            answers["G4"] = {
                "selected_letter": g1 if g1 in LETTERS and g1 == g2 else tail,
                "route": "graph_stable" if g1 in LETTERS and g1 == g2 else "tail_fallback",
                "votes": {"G1": g1, "G2": g2, "B1": tail},
            }
            g5, route = majority_or_tail(
                [answers[method]["selected_letter"] for method in ("G1", "G2", "G3")],
                tail,
            )
            answers["G5"] = {
                "selected_letter": g5,
                "route": route,
                "votes": {
                    method: answers[method]["selected_letter"]
                    for method in ("G1", "G2", "G3", "B1")
                },
            }
            invalid = [method for method in METHODS if answers[method]["selected_letter"] not in LETTERS]
            if invalid:
                raise RuntimeError(f"{novel}/q{qi}: invalid answers for {invalid}")

            gold = LETTERS[question["gold_index"]]
            row = {
                "version": VERSION,
                "source_hash": source_hash,
                "model": args.model,
                "graph_sha256": graph_sha256,
                "novel": novel,
                "qi": qi,
                "qid": question["qid"],
                "question": question["question"],
                "choices": question["choices"],
                "gold_letter": gold,
                "answers": answers,
                "correct": {method: answers[method]["selected_letter"] == gold for method in METHODS},
                "attention_features": {
                    "option_order_unanimous": len(
                        {answers[method]["selected_letter"] for method in ("G1", "G2", "G3")}
                    )
                    == 1,
                    "graph_retrieved_chunks": len(answers["G1"]["retrieval"]["chunks"]),
                    "graph_relation_hints": len(answers["G1"]["retrieval"]["links"]),
                    "rag_retrieved_chunks": len(answers["B3"]["retrieval"]["chunks"]),
                },
            }
            answer_path.parent.mkdir(parents=True, exist_ok=True)
            answer_path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
            completed += 1
            elapsed = max(time.time() - started, 0.01)
            progress = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "completed_questions": completed,
                "total_questions": total,
                "current": f"{novel}/q{qi}",
                "per_hour": completed / elapsed * 3600,
                "eta_minutes": (total - completed) / max(completed / elapsed, 1e-9) / 60,
            }
            (args.out_root / "progress.json").write_text(
                json.dumps(progress, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            print(
                f"[{completed}/{total}] {novel}/q{qi} "
                + " ".join(f"{method}={answers[method]['selected_letter']}" for method in METHODS),
                flush=True,
            )

    report = analyze(args.out_root, args.novels)
    manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    manifest["questions"] = report["metadata"]["questions"]
    (args.out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(json.dumps(report["all"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
