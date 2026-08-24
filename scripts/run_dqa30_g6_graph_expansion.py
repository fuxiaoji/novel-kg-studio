"""G6: graph-guided source expansion with signed option dossiers.

Unlike G1, G6 lets graph-ranked chunks replace ordinary retrieval chunks. It
keeps a six-chunk budget, filters malformed relations, grounds every surviving
relation in a selected source chunk, and asks one Qwen3.5-9B call to compare all
four option dossiers. Results are cached per question.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import defaultdict
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
from novel_kg_studio.schema import norm_text  # noqa: E402

VERSION = "g6-graph-guided-source-expansion-signed-dossier-v1"
NOVELS = ["93", "97", "99", "100", "103", "104", "105", "106", "107", "108"]
INVALID_ENTITY = re.compile(r"^(?:letter|option|candidate|choice|answer)\s*[-_: ]*[a-d0-9]*$", re.I)
DIRECT_RELATIONS = {"supports", "contradicts", "motive", "means", "opportunity", "witnessed_by", "temporal_sequence", "belongs_to"}


def options_text(question: dict[str, Any]) -> str:
    return "\n".join(f"{LETTERS[index]}. {choice}" for index, choice in enumerate(question["choices"][:4]))


def clean_link(link: dict[str, Any], chunk_text: str) -> bool:
    source = str(link.get("source", "")).strip()
    target = str(link.get("target", "")).strip()
    evidence = str(link.get("evidence", "")).strip()
    relation = str(link.get("relation", "")).strip()
    if not source or not target or INVALID_ENTITY.match(source) or INVALID_ENTITY.match(target):
        return False
    if norm_text(source) == norm_text(target) or relation not in DIRECT_RELATIONS:
        return False
    normalized_evidence = norm_text(evidence)
    if len(normalized_evidence) < 18 or normalized_evidence not in norm_text(chunk_text):
        return False
    return True


def build_dossier(
    ctx: C8Context,
    matrix: np.ndarray,
    option_vectors: np.ndarray,
    question: dict[str, Any],
    *,
    chunk_limit: int = 6,
    link_limit: int = 6,
) -> dict[str, Any]:
    packets = [_option_packet(ctx, matrix, option_vectors[index], question, index) for index in range(4)]
    by_chunk: dict[str, dict[str, Any]] = {}
    ranked_rows: list[tuple[float, int, int, dict[str, Any]]] = []
    for option_index, packet in enumerate(packets):
        for rank, chunk in enumerate(packet["chunks"]):
            score = float(chunk.get("rrf_score") or 0.0)
            ranked_rows.append((score, option_index, rank, chunk))
            item = by_chunk.setdefault(
                chunk["id"],
                {**chunk, "for_options": [], "best_rank": rank, "best_score": score},
            )
            item["for_options"].append(LETTERS[option_index])
            item["best_rank"] = min(item["best_rank"], rank)
            item["best_score"] = max(item["best_score"], score)

    selected_ids: list[str] = []
    # Guarantee at least one graph-ranked passage for each option.
    for option_index in range(4):
        candidates = sorted(
            (row for row in ranked_rows if row[1] == option_index),
            key=lambda row: (-row[0], row[2]),
        )
        for _, _, _, chunk in candidates:
            if chunk["id"] not in selected_ids:
                selected_ids.append(chunk["id"])
                break
    for _, _, _, chunk in sorted(ranked_rows, key=lambda row: (-row[0], row[2])):
        if chunk["id"] not in selected_ids:
            selected_ids.append(chunk["id"])
        if len(selected_ids) >= chunk_limit:
            break
    selected_ids = selected_ids[:chunk_limit]
    selected = [by_chunk[chunk_id] for chunk_id in selected_ids]
    selected.sort(key=lambda row: int(row["start"]))
    selected_by_id = {row["id"]: row for row in selected}

    valid_links: list[dict[str, Any]] = []
    removed_links = 0
    seen = set()
    for packet in packets:
        for link in packet["links"]:
            chunk = selected_by_id.get(link["chunk_id"])
            key = (packet["letter"], link.get("source"), link.get("relation"), link.get("target"), link.get("chunk_id"))
            if chunk is None or key in seen or not clean_link(link, chunk["text"]):
                removed_links += 1
                continue
            seen.add(key)
            valid_links.append({"candidate": packet["letter"], **link})
    valid_links.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
    valid_links = valid_links[:link_limit]

    option_mass = defaultdict(float)
    for link in valid_links:
        option_mass[link["candidate"]] += float(link.get("score") or 0.0)
    masses = sorted(option_mass.values(), reverse=True)
    graph_margin = masses[0] - masses[1] if len(masses) >= 2 else masses[0] if masses else 0.0
    return {
        "chunks": selected,
        "links": valid_links,
        "diagnostics": {
            "selected_chunk_ids": selected_ids,
            "valid_relation_count": len(valid_links),
            "removed_relation_count": removed_links,
            "option_relation_mass": dict(option_mass),
            "graph_margin": graph_margin,
            "source_chars": sum(len(row["text"]) for row in selected),
        },
    }


def answer_prompt(question: dict[str, Any], dossier: dict[str, Any]) -> str:
    passages = "\n\n".join(
        f"[{row['id']} | novel position {int(row['start'])} | candidates {','.join(sorted(set(row['for_options'])))}]\n{row['text']}"
        for row in dossier["chunks"]
    )
    by_option: dict[str, list[str]] = defaultdict(list)
    for row in dossier["links"]:
        sign = "REFUTES" if row["relation"] == "contradicts" else "HYPOTHESIS"
        by_option[row["candidate"]].append(
            f"{sign}: {row['source']} --{row['relation']}--> {row['target']} "
            f"[{row['chunk_id']}; source: {row['evidence'][:260]}]"
        )
    graph_rows = []
    for index, choice in enumerate(question["choices"][:4]):
        letter = LETTERS[index]
        graph_rows.append(
            f"{letter}. {choice}\n" + ("\n".join(f"  - {item}" for item in by_option.get(letter, [])) or "  - no grounded graph path")
        )
    return (
        f"QUESTION\n{question['question']}\n\nOPTIONS\n{options_text(question)}\n\n"
        f"GRAPH-GUIDED ORIGINAL PASSAGES\n{passages}\n\nSIGNED OPTION DOSSIERS\n" + "\n\n".join(graph_rows)
        + "\n\nThe graph paths are retrieval hypotheses, not facts. Accept a path only when its quoted source passage "
        "directly supports the option. Reject mistranslated aliases, option-letter entities, self-relations, early suspicions, "
        "and relations that answer a different question. Compare all four options. Prefer explicit final revelations. "
        "For NOT/EXCEPT questions, reverse the requested condition carefully. Return strict JSON only: "
        '{"selected_letter":"A|B|C|D","confidence":"high|medium|low","support_ids":["c_1"],"reason":"brief comparison"}'
    )


def valid_cache(path: Path, graph_sha: str, source_hash: str) -> bool:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return (
            row.get("version") == VERSION
            and row.get("graph_sha256") == graph_sha
            and row.get("source_hash") == source_hash
            and row.get("selected_letter") in LETTERS
        )
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--graph-root", type=Path, default=BASE_PATH / "dqa30_attention" / "batch03" if False else ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "batch03")
    parser.add_argument("--baseline-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "batch03_eval")
    parser.add_argument("--out-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "g6_graph_expansion")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--num-ctx", type=int, default=16384)
    args = parser.parse_args()

    args.out_root.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    client = NativeOllamaNoThinkClient(args.model, num_ctx=args.num_ctx)
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]
    total = sum(len(cases[novel]["questions"]) for novel in args.novels)
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
            for question in case["questions"]
            for choice in question["choices"][:4]
        ]
        query_matrix = embed(queries)
        for qi, question in enumerate(case["questions"]):
            answer_path = args.out_root / "answers" / novel / f"q{qi:02d}.json"
            if valid_cache(answer_path, graph_sha, source_hash):
                completed += 1
                continue
            vectors = query_matrix[qi * 4 : qi * 4 + 4]
            dossier = build_dossier(ctx, matrix, vectors, question)
            raw = client.complete_json(
                "You are a conservative detective-novel evidence judge. Do not use outside knowledge.",
                answer_prompt(question, dossier),
                max_tokens=260,
            )
            letter = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw)
            if letter not in LETTERS:
                raw = client.complete_json(
                    "Return one JSON multiple-choice answer from the supplied evidence.",
                    answer_prompt(question, dossier),
                    max_tokens=180,
                )
                letter = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw)
            if letter not in LETTERS:
                raise RuntimeError(f"unparsed answer for {novel}/q{qi}: {raw}")
            baseline_path = args.baseline_root / "answers" / novel / f"q{qi:02d}.json"
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            baseline_chunks = {row["id"] for row in baseline["answers"]["B3"]["retrieval"]["chunks"]}
            graph_chunks = set(dossier["diagnostics"]["selected_chunk_ids"])
            output = {
                "version": VERSION,
                "source_hash": source_hash,
                "model": args.model,
                "graph_sha256": graph_sha,
                "novel": novel,
                "qi": qi,
                "qid": question["qid"],
                "question": question["question"],
                "choices": question["choices"],
                "gold_letter": LETTERS[int(question["gold_index"])],
                "selected_letter": letter,
                "correct": letter == LETTERS[int(question["gold_index"])],
                "confidence": str(raw.get("confidence", "")) if isinstance(raw, dict) else "",
                "support_ids": raw.get("support_ids", []) if isinstance(raw, dict) else [],
                "reason": str(raw.get("reason", "")) if isinstance(raw, dict) else "",
                "retrieval": dossier,
                "confidence_features": {
                    **dossier["diagnostics"],
                    "graph_only_chunks": len(graph_chunks - baseline_chunks),
                    "rag_overlap": len(graph_chunks & baseline_chunks) / max(len(graph_chunks | baseline_chunks), 1),
                    "g1_agreement": letter == baseline["answers"]["G1"]["selected_letter"],
                    "g5_agreement": letter == baseline["answers"]["G5"]["selected_letter"],
                    "b2_agreement": letter == baseline["answers"]["B2"]["selected_letter"],
                    "b3_agreement": letter == baseline["answers"]["B3"]["selected_letter"],
                },
                "raw": raw,
            }
            answer_path.parent.mkdir(parents=True, exist_ok=True)
            answer_path.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")
            completed += 1
            elapsed = max(time.time() - started, 1)
            progress = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "completed": completed,
                "total": total,
                "current": f"{novel}/q{qi}",
                "accuracy_so_far": sum(
                    json.loads(path.read_text(encoding="utf-8")).get("correct", False)
                    for path in (args.out_root / "answers").rglob("q*.json")
                ) / completed,
                "per_hour": 3600 * completed / elapsed,
            }
            (args.out_root / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[{completed}/{total}] {novel}/q{qi} G6={letter} gold={output['gold_letter']} links={dossier['diagnostics']['valid_relation_count']} graph_only={output['confidence_features']['graph_only_chunks']}", flush=True)


if __name__ == "__main__":
    main()
