"""C8: compact graph-guided passage retrieval for small-context readers.

The existing option-aware methods rank nodes using edge evidence but mostly map
node evidence back to source chunks. C8 makes edge evidence first-class, spreads
query mass over reliable graph relations, and gives the reader a small,
chronologically organized set of original passages with a minimal answer schema.
"""

from __future__ import annotations

import bisect
import copy
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

from c_option_methods import LETTERS, EvidenceContext, normalize_letter, question_type, strict_visible_text
from novel_kg_studio.chunking import TextChunk, chunk_text
from novel_kg_studio.schema import norm_text
from novel_kg_studio.store.bm25 import BM25Index

VERSION = "c8-verified-link-overlay-v2"
LOW_VALUE_RELATIONS = {"related_to", "mentions", "located_at", "appears_at"}
HIGH_VALUE_RELATIONS = {"supports", "contradicts", "motive", "means", "opportunity", "witnessed_by", "belongs_to", "temporal_sequence"}


class NormalizedLocator:
    """Locate normalized evidence while preserving offsets in the original text."""

    def __init__(self, text: str) -> None:
        self.text = text
        chars: list[str] = []
        original: list[int] = []
        in_space = True
        for index, char in enumerate(text.lower()):
            if char.isspace():
                if not in_space and chars:
                    chars.append(" ")
                    original.append(index)
                in_space = True
            else:
                chars.append(char)
                original.append(index)
                in_space = False
        if chars and chars[-1] == " ":
            chars.pop()
            original.pop()
        self.normalized = "".join(chars)
        self.original = original

    def find(self, evidence: str, near_ratio: float | None = None) -> int:
        target = norm_text(evidence)
        if not target:
            return -1
        starts: list[int] = []
        cursor = 0
        while True:
            hit = self.normalized.find(target, cursor)
            if hit < 0:
                break
            starts.append(hit)
            cursor = hit + max(1, len(target))
            if len(starts) >= 32:
                break
        if not starts:
            return -1
        if near_ratio is None or len(starts) == 1:
            chosen = starts[0]
        else:
            expected = max(0, min(len(self.text) - 1, int(near_ratio * len(self.text))))
            chosen = min(starts, key=lambda pos: abs(self.original[min(pos, len(self.original) - 1)] - expected))
        return self.original[min(chosen, len(self.original) - 1)]


@dataclass
class C8Context:
    base: EvidenceContext
    locator: NormalizedLocator
    chunk_starts: list[int]
    node_to_chunks: dict[str, set[int]]
    edge_to_chunks: dict[int, set[int]]

    @classmethod
    def build(cls, graph: dict[str, Any], novel_text: str, mask_char: int | None) -> "C8Context":
        base = EvidenceContext.build(graph, novel_text, mask_char)
        locator = NormalizedLocator(base.novel_text)
        starts = [chunk.start for chunk in base.chunks]

        def chunk_ids(evidence: str, near_ratio: float | None = None) -> set[int]:
            pos = locator.find(evidence, near_ratio)
            if pos < 0:
                return set()
            idx = max(0, min(len(base.chunks) - 1, bisect.bisect_right(starts, pos) - 1))
            result = {idx}
            # Include the adjacent chunk when evidence is close to an overlap boundary.
            if idx + 1 < len(base.chunks) and base.chunks[idx + 1].start <= pos + len(str(evidence)):
                result.add(idx + 1)
            return result

        node_to_chunks: dict[str, set[int]] = defaultdict(set)
        for node in base.store.nodes:
            ratio = _float_or_none(node.get("text_pos"))
            for evidence in node.get("evidence", [])[:8]:
                node_to_chunks[node["id"]].update(chunk_ids(str(evidence), ratio))
        edge_to_chunks: dict[int, set[int]] = defaultdict(set)
        for edge_index, edge in enumerate(base.store.edges):
            ratios = [
                _float_or_none(base.store.by_id.get(edge.get("source"), {}).get("text_pos")),
                _float_or_none(base.store.by_id.get(edge.get("target"), {}).get("text_pos")),
            ]
            valid_ratios = [value for value in ratios if value is not None]
            near = sum(valid_ratios) / len(valid_ratios) if valid_ratios else None
            edge_to_chunks[edge_index].update(chunk_ids(str(edge.get("evidence", "")), near))
        return cls(base, locator, starts, node_to_chunks, edge_to_chunks)


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
        return result if 0 <= result <= 1 else None
    except (TypeError, ValueError):
        return None


def _normalized_scores(index: BM25Index, query: str) -> np.ndarray:
    scores = index.score(query)
    maximum = float(scores.max()) if len(scores) else 0.0
    return scores / maximum if maximum > 0 else scores


def _node_seed_scores(ctx: C8Context, question: str, choices: list[str]) -> np.ndarray:
    store = ctx.base.store
    seeds = 0.45 * _normalized_scores(ctx.base.node_index, question)
    for choice in choices[:4]:
        seeds = np.maximum(seeds, 0.30 * _normalized_scores(ctx.base.node_index, f"{question} {choice}"))
    query_norm = norm_text(question + " " + " ".join(choices[:4]))
    for index, node in enumerate(store.nodes):
        names = [str(node.get("name", "")), *[str(alias) for alias in node.get("aliases", [])]]
        for name in names:
            canonical = norm_text(name)
            if len(canonical) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(canonical)}(?![a-z0-9])", query_norm):
                seeds[index] += 1.0
                break
    total = float(seeds.sum())
    return seeds / total if total > 0 else np.full(len(store.nodes), 1 / max(len(store.nodes), 1))


def personalized_graph_scores(ctx: C8Context, question: str, choices: list[str], iterations: int = 16) -> np.ndarray:
    store = ctx.base.store
    if not store.nodes:
        return np.zeros(0)
    seed = _node_seed_scores(ctx, question, choices)
    by_id_index = {node["id"]: index for index, node in enumerate(store.nodes)}
    transitions: list[list[tuple[int, float]]] = [[] for _ in store.nodes]
    for edge in store.edges:
        source = by_id_index.get(edge.get("source"))
        target = by_id_index.get(edge.get("target"))
        if source is None or target is None:
            continue
        relation = str(edge.get("type", ""))
        weight = max(0.05, float(edge.get("confidence") or 0.6))
        weight *= 1.45 if relation in HIGH_VALUE_RELATIONS else 0.35 if relation in LOW_VALUE_RELATIONS else 1.0
        if edge.get("evidence"):
            weight *= 1.15
        transitions[source].append((target, weight))
        transitions[target].append((source, weight))
    rank = seed.copy()
    alpha = 0.78
    for _ in range(iterations):
        updated = (1 - alpha) * seed
        dangling = 0.0
        for source, neighbors in enumerate(transitions):
            if not neighbors:
                dangling += rank[source]
                continue
            denom = sum(weight for _, weight in neighbors)
            for target, weight in neighbors:
                updated[target] += alpha * rank[source] * weight / denom
        updated += alpha * dangling * seed
        rank = updated
    return rank


def _chunk_row(chunk: TextChunk, score: float, source: str) -> dict[str, Any]:
    return {"id": chunk.id, "start": chunk.start, "end": chunk.end, "text": chunk.text, "score": float(score), "source": source}


def retrieve_bm25(ctx: C8Context, q: dict[str, Any], limit: int = 18) -> dict[str, Any]:
    question = q["question"]
    choices = q["choices"][:4]
    qscore = _normalized_scores(ctx.base.chunk_index, question)
    option_scores = [_normalized_scores(ctx.base.chunk_index, f"{question} {choice}") for choice in choices]
    combined = 0.65 * qscore
    for scores in option_scores:
        combined = np.maximum(combined, 0.55 * scores)
    order = np.argsort(combined)[::-1]
    selected = _select_diverse(order, combined, limit, len(ctx.base.chunks))
    return {"chunks": [_chunk_row(ctx.base.chunks[i], combined[i], "bm25") for i in sorted(selected)], "links": [], "novel_length": len(ctx.base.novel_text), "diagnostics": {"selected": selected}}


def retrieve_graph(ctx: C8Context, q: dict[str, Any], limit: int = 18) -> dict[str, Any]:
    question = q["question"]
    choices = q["choices"][:4]
    qscore = _normalized_scores(ctx.base.chunk_index, question)
    option_scores = [_normalized_scores(ctx.base.chunk_index, f"{question} {choice}") for choice in choices]
    lexical = 0.55 * qscore
    for scores in option_scores:
        lexical = np.maximum(lexical, 0.45 * scores)
    node_rank = personalized_graph_scores(ctx, question, choices)
    chunk_graph = np.zeros(len(ctx.base.chunks), dtype=float)
    by_id_index = {node["id"]: index for index, node in enumerate(ctx.base.store.nodes)}
    for node_id, chunk_ids in ctx.node_to_chunks.items():
        index = by_id_index.get(node_id)
        if index is None:
            continue
        for chunk_id in chunk_ids:
            chunk_graph[chunk_id] += node_rank[index]
    edge_rows = []
    for edge_index, edge in enumerate(ctx.base.store.edges):
        source = by_id_index.get(edge.get("source"))
        target = by_id_index.get(edge.get("target"))
        if source is None or target is None:
            continue
        relation = str(edge.get("type", ""))
        relation_weight = 1.4 if relation in HIGH_VALUE_RELATIONS else 0.3 if relation in LOW_VALUE_RELATIONS else 1.0
        mass = relation_weight * float(edge.get("confidence") or 0.6) * (node_rank[source] + node_rank[target])
        for chunk_id in ctx.edge_to_chunks.get(edge_index, set()):
            chunk_graph[chunk_id] += mass
            edge_rows.append((mass, edge_index, chunk_id))
    maximum = float(chunk_graph.max()) if len(chunk_graph) else 0.0
    if maximum > 0:
        chunk_graph /= maximum
    # Graph mass ranks relation hints. It does not evict source passages: an
    # offline development-set audit found unconditional expansion reduced gold
    # clue recall. This verified overlay keeps the full matched BM25 set.
    combined = 0.48 * lexical + 0.52 * chunk_graph
    # KG²RAG-style policy: preserve strong ordinary-RAG seed passages, then use
    # the graph only to organize and connect them.
    seed_package = retrieve_bm25(ctx, q, limit=limit)
    selected = list(seed_package["diagnostics"]["selected"])
    selected_set = set(selected)
    links = []
    seen_edges = set()
    for mass, edge_index, chunk_id in sorted(edge_rows, reverse=True):
        if chunk_id not in selected_set or edge_index in seen_edges:
            continue
        edge = ctx.base.store.edges[edge_index]
        relation = str(edge.get("type", ""))
        if relation not in HIGH_VALUE_RELATIONS:
            continue
        source = ctx.base.store.by_id.get(edge.get("source"), {})
        target = ctx.base.store.by_id.get(edge.get("target"), {})
        source_name = str(source.get("name", "")).strip()
        target_name = str(target.get("name", "")).strip()
        if not source_name or not target_name or norm_text(source_name) == norm_text(target_name):
            continue
        links.append(
            {
                "source": source_name,
                "relation": relation,
                "target": target_name,
                "chunk_id": ctx.base.chunks[chunk_id].id,
                "evidence": str(edge.get("evidence", "")).strip()[:260],
                "score": mass,
            }
        )
        seen_edges.add(edge_index)
        if len(links) >= 6:
            break
    chunks = [_chunk_row(ctx.base.chunks[i], combined[i], "bm25_with_graph_overlay") for i in sorted(selected)]
    return {
        "chunks": chunks,
        "links": links,
        "novel_length": len(ctx.base.novel_text),
        "diagnostics": {
            "selected": selected,
            "edge_evidence_located": sum(bool(v) for v in ctx.edge_to_chunks.values()),
            "node_evidence_located": sum(bool(v) for v in ctx.node_to_chunks.values()),
            "graph_mass_nonzero_chunks": int(np.count_nonzero(chunk_graph)),
        },
    }


def _select_diverse(order: Any, scores: np.ndarray, limit: int, total: int) -> list[int]:
    selected: list[int] = []
    seen = set()
    for raw in order:
        index = int(raw)
        if index < 0 or index >= total or index in seen:
            continue
        # Neighboring overlapping chunks add little information; retain only
        # when their score is materially stronger than the existing neighbor.
        neighbor = next((old for old in selected if abs(old - index) <= 1), None)
        if neighbor is not None and float(scores[index]) <= 1.25 * float(scores[neighbor]):
            continue
        if neighbor is not None:
            selected.remove(neighbor)
            seen.discard(neighbor)
        selected.append(index)
        seen.add(index)
        if len(selected) >= limit:
            break
    return selected


def answer_prompt(q: dict[str, Any], package: dict[str, Any]) -> str:
    options = "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(q["choices"][:4]))
    chunks = []
    novel_length = max(int(package.get("novel_length", 1)), 1)
    for row in package["chunks"]:
        chunks.append(f"[{row['id']} | source position {row['start'] / novel_length:.0%}]\n{row['text']}")
    links = "\n".join(
        f"- {row['source']} --{row['relation']}--> {row['target']} "
        f"(verify in {row['chunk_id']}: {row.get('evidence', '')})"
        for row in package.get("links", [])
    )
    graph_section = (
        "\n\nGRAPH INDEX HINTS (may be noisy; use only when verified by source passages)\n" + links
        if links else ""
    )
    return (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options}\n\n"
        "SOURCE PASSAGES IN NOVEL ORDER\n" + "\n\n".join(chunks) + graph_section
        + "\n\nChoose the option best supported by the source passages. Detective stories may contain early false leads: "
        "prefer later explicit resolution and direct evidence over suspicion, genre convention, or option wording. "
        "For a negative/EXCEPT question, identify the explicitly false option. Return strict JSON only: "
        '{"selected_letter":"A|B|C|D","reason":"one short evidence-based sentence"}'
    )


def run_c8(method: str, client: Any, q: dict[str, Any], graph: dict[str, Any], novel_text: str, mask_char: int | None = None) -> dict[str, Any]:
    started = time.time()
    ctx = C8Context.build(graph, novel_text, mask_char)
    package = retrieve_graph(ctx, q) if method == "graph" else retrieve_bm25(ctx, q)
    raw = client.complete_json(
        "You are a careful small-context detective-novel reader. Use only supplied passages and return one answer.",
        answer_prompt(q, package),
        max_tokens=500,
    )
    letter = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw)
    return {
        "method": f"c8_{method}_minimal",
        "selected_letter": letter,
        "selected_text": q["choices"][LETTERS.index(letter)] if letter in LETTERS else "",
        "reason": str(raw.get("reason", "")) if isinstance(raw, dict) else "",
        "retrieval": package,
        "raw": raw,
        "question_type": question_type(q["question"]),
        "mask_policy": ctx.base.mask_policy,
        "masked_at": mask_char,
        "elapsed_seconds": round(time.time() - started, 3),
        "prompt_version": VERSION,
    }
