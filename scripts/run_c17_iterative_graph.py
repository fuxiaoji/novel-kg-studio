"""C17: local candidate-aware graph navigation with a bounded falsification reread."""

from __future__ import annotations

import argparse
import hashlib
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
from c8_graph_passage import C8Context, HIGH_VALUE_RELATIONS, LETTERS, normalize_letter  # noqa: E402
from native_ollama_client import NativeOllamaNoThinkClient  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, graph_path  # noqa: E402
from run_local_smallmodel_pilot import option_text_packet  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
CACHE = BASE / "dqa_c17_edge_embedding_cache"
VERSION = "c17-local-iterative-candidate-edge-navigation-v1"


def edge_documents(ctx: C8Context) -> tuple[list[int], list[str]]:
    indices: list[int] = []
    documents: list[str] = []
    for index, edge in enumerate(ctx.base.store.edges):
        relation = str(edge.get("type", ""))
        evidence = str(edge.get("evidence", "")).strip()
        if relation not in HIGH_VALUE_RELATIONS or len(evidence) < 20 or not ctx.edge_to_chunks.get(index):
            continue
        source = ctx.base.store.by_id.get(edge.get("source"), {})
        target = ctx.base.store.by_id.get(edge.get("target"), {})
        source_name = str(source.get("name", "")).strip()
        target_name = str(target.get("name", "")).strip()
        if not source_name or not target_name or source_name.casefold() == target_name.casefold():
            continue
        indices.append(index)
        documents.append(f"{source_name} {relation.replace('_', ' ')} {target_name}. Evidence: {evidence[:500]}")
    return indices, documents


def cached_edge_embeddings(novel: str, documents: list[str]) -> np.ndarray:
    CACHE.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256("\n<edge>\n".join(documents).encode("utf-8")).hexdigest()
    path = CACHE / f"{novel}.npz"
    if path.exists():
        data = np.load(path, allow_pickle=False)
        if str(data["digest"].item()) == digest:
            return data["embeddings"]
    blocks = [embed(documents[start : start + 32]) for start in range(0, len(documents), 32)]
    matrix = np.vstack(blocks) if blocks else np.zeros((0, 1024), dtype=np.float32)
    np.savez_compressed(path, embeddings=matrix, digest=np.asarray(digest))
    return matrix


def option_queries(q: dict[str, Any]) -> list[str]:
    return [f"Question: {q['question']}\nCandidate answer: {choice}" for choice in q["choices"][:4]]


def choose_edges(
    ctx: C8Context,
    edge_indices: list[int],
    edge_matrix: np.ndarray,
    query_matrix: np.ndarray,
    *,
    per_option: int = 2,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    if not len(edge_indices):
        return selected
    scores = edge_matrix @ query_matrix.T
    for option_index, letter in enumerate(LETTERS):
        kept = 0
        for local_index in np.argsort(scores[:, option_index])[::-1]:
            edge_index = edge_indices[int(local_index)]
            if edge_index in seen:
                continue
            edge = ctx.base.store.edges[edge_index]
            chunks = sorted(ctx.edge_to_chunks.get(edge_index, set()))
            if not chunks:
                continue
            source = ctx.base.store.by_id.get(edge.get("source"), {})
            target = ctx.base.store.by_id.get(edge.get("target"), {})
            chunk_index = chunks[-1]
            selected.append(
                {
                    "for_option": letter,
                    "edge_index": edge_index,
                    "source": source.get("name", ""),
                    "relation": edge.get("type", ""),
                    "target": target.get("name", ""),
                    "evidence": str(edge.get("evidence", ""))[:500],
                    "chunk_index": chunk_index,
                    "score": float(scores[int(local_index), option_index]),
                }
            )
            seen.add(edge_index)
            kept += 1
            if kept >= per_option:
                break
    return selected


def add_chunk(chunks: dict[int, dict[str, Any]], ctx: C8Context, index: int, source: str, for_option: str) -> None:
    if not 0 <= index < len(ctx.base.chunks):
        return
    row = chunks.setdefault(
        index,
        {
            "id": ctx.base.chunks[index].id,
            "index": index,
            "start": ctx.base.chunks[index].start,
            "end": ctx.base.chunks[index].end,
            "text": ctx.base.chunks[index].text,
            "sources": [],
            "for_options": [],
        },
    )
    row["sources"].append(source)
    row["for_options"].append(for_option)


def stage_one_package(
    q: dict[str, Any],
    ctx: C8Context,
    chunk_matrix: np.ndarray,
    edge_indices: list[int],
    edge_matrix: np.ndarray,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    qmatrix = embed(option_queries(q))
    chunks: dict[int, dict[str, Any]] = {}
    for option_index, letter in enumerate(LETTERS):
        packet = option_text_packet(ctx, chunk_matrix, qmatrix[option_index], q, option_index)
        for row in packet["chunks"][:2]:
            add_chunk(chunks, ctx, int(row["index"]), "text_rrf", letter)
    edges = choose_edges(ctx, edge_indices, edge_matrix, qmatrix, per_option=2)
    for row in edges:
        add_chunk(chunks, ctx, int(row["chunk_index"]), "dense_graph_edge", str(row["for_option"]))
    return sorted(chunks.values(), key=lambda row: row["start"]), edges


def render_chunks(chunks: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"[{row['id']} | candidates {','.join(sorted(set(row['for_options'])))} | {','.join(sorted(set(row['sources'])))}]\n{row['text']}"
        for row in chunks
    )


def render_edges(edges: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"- candidate {row['for_option']}: {row['source']} --{row['relation']}--> {row['target']} "
        f"[{ctx_id(row['chunk_index'])}]; evidence: {row['evidence']}"
        for row in edges
    )


def ctx_id(index: int) -> str:
    return f"c_{index}"


def options_text(q: dict[str, Any]) -> str:
    return "\n".join(f"{LETTERS[index]}. {choice}" for index, choice in enumerate(q["choices"][:4]))


def answer_one(
    client: NativeOllamaNoThinkClient,
    q: dict[str, Any],
    ctx: C8Context,
    chunk_matrix: np.ndarray,
    edge_indices: list[int],
    edge_matrix: np.ndarray,
    *,
    followup: bool = True,
) -> dict[str, Any]:
    chunks, edges = stage_one_package(q, ctx, chunk_matrix, edge_indices, edge_matrix)
    prompt1 = (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options_text(q)}\n\nORIGINAL NOVEL PASSAGES\n{render_chunks(chunks)}\n\n"
        f"CANDIDATE-MATCHED GRAPH EDGES (navigation hints only)\n{render_edges(edges)}\n\n"
        "Compare all four answer claims. Original passages have authority over graph edges. Prefer final revelation over "
        "hypotheses and red herrings. Missing evidence is unknown, not contradiction. Obey NOT/EXCEPT wording. Return JSON only: "
        '{"selected_letter":"A|B|C|D","runner_up":"A|B|C|D","missing_fact":"one concrete fact that would distinguish them"}'
    )
    first_raw = client.complete_json("Solve from compact graph-navigated novel evidence. Do not reveal chain of thought.", prompt1, max_tokens=180)
    first = normalize_letter(first_raw.get("selected_letter") if isinstance(first_raw, dict) else first_raw) or "A"
    runner = normalize_letter(first_raw.get("runner_up") if isinstance(first_raw, dict) else None)
    if runner == first or runner not in LETTERS:
        runner = next(letter for letter in LETTERS if letter != first)
    missing = str(first_raw.get("missing_fact", "") if isinstance(first_raw, dict) else "")[:500]

    if not followup:
        return {
            "selected_letter": first,
            "stage1_letter": first,
            "runner_up": runner,
            "stage1_raw": first_raw,
            "stage2_raw": None,
            "retrieval": {"stage1_chunks": chunks, "stage1_edges": edges, "followup_chunk_count": 0, "followup_edges": []},
        }

    focus = f"{q['question']} Candidate {first}: {q['choices'][LETTERS.index(first)] if first in LETTERS else ''}. Candidate {runner}: {q['choices'][LETTERS.index(runner)]}. Missing fact: {missing}"
    focus_vector = embed([focus])[0]
    dense_order = list(map(int, np.argsort(chunk_matrix @ focus_vector)[::-1]))
    follow_chunks: dict[int, dict[str, Any]] = {int(row["index"]): dict(row) for row in chunks}
    added = 0
    for index in dense_order:
        if index in follow_chunks or any(abs(index - old) <= 1 for old in follow_chunks):
            continue
        add_chunk(follow_chunks, ctx, index, "followup_missing_fact", f"{first},{runner}")
        for neighbor in (index - 1, index + 1):
            add_chunk(follow_chunks, ctx, neighbor, "narrative_neighbor", f"{first},{runner}")
        added += 1
        if added >= 3:
            break
    focus_edges = choose_edges(ctx, edge_indices, edge_matrix, np.vstack([focus_vector] * 4), per_option=1)
    prompt2 = (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options_text(q)}\n\nPROVISIONAL RESULT\n"
        f"selected={first}; strongest rival={runner}; unresolved fact={missing}\n\nEXPANDED ORIGINAL PASSAGES\n"
        f"{render_chunks(sorted(follow_chunks.values(), key=lambda row: row['start']))}\n\nTARGETED GRAPH EDGES\n{render_edges(focus_edges)}\n\n"
        "Act as a falsifier. Try to disprove the provisional answer using an explicit different identity, action, place, time, "
        "number, or motive. Do not change it merely because a rival is mentioned. Graph edges are hints and must agree with "
        "the original passages. Prefer the final solution. Return JSON only: "
        '{"selected_letter":"A|B|C|D","decision":"keep|change","decisive_evidence":"brief"}'
    )
    second_raw = client.complete_json("Perform one bounded evidence-based falsification reread. No chain of thought.", prompt2, max_tokens=180)
    second = normalize_letter(second_raw.get("selected_letter") if isinstance(second_raw, dict) else second_raw) or first
    return {
        "selected_letter": second,
        "stage1_letter": first,
        "runner_up": runner,
        "stage1_raw": first_raw,
        "stage2_raw": second_raw,
        "retrieval": {"stage1_chunks": chunks, "stage1_edges": edges, "followup_chunk_count": len(follow_chunks), "followup_edges": focus_edges},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--skip-followup", action="store_true")
    parser.add_argument("--out", type=Path, default=BASE / "dqa_local_c17_iterative20")
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    client = NativeOllamaNoThinkClient(args.model)
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        chunk_matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        edge_indices, documents = edge_documents(ctx)
        edge_matrix = cached_edge_embeddings(novel, documents)
        questions = case["questions"][: args.max_questions or None]
        for qi, q in enumerate(questions):
            path = args.out / "answers" / args.model.replace(":", "_") / novel / f"q{qi:02d}.json"
            if path.exists():
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                    if old.get("version") == VERSION and old.get("selected_letter") in LETTERS:
                        continue
                except Exception:
                    pass
            result = answer_one(client, q, ctx, chunk_matrix, edge_indices, edge_matrix, followup=not args.skip_followup)
            gold = LETTERS[q["gold_index"]]
            result.update(
                {
                    "version": VERSION,
                    "model": args.model,
                    "thinking": "disabled",
                    "external_api": False,
                    "mask": "unmasked",
                    "novel": novel,
                    "batch": "first10" if novel in FIRST10 else "second10",
                    "qi": qi,
                    "qid": q["qid"],
                    "question": q["question"],
                    "choices": q["choices"],
                    "gold_letter": gold,
                    "correct": result["selected_letter"] == gold,
                }
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[{args.model}/{novel}/q{qi}] {result['stage1_letter']}->{result['selected_letter']} gold={gold}", flush=True)


if __name__ == "__main__":
    main()
