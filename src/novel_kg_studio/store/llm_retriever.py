"""LLM-driven retrieval: search planning + HyDE-style expansion + typed/salience/decoy scoring."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..cache import load_json, save_json
from .bm25 import BM25Index

TYPE_BOOST = {
    "clue_object": 1.6,
    "location": 1.3,
    "time_anchor": 1.3,
    "evidence_sentence": 1.2,
    "event": 1.1,
    "person": 1.0,
}


@dataclass
class SearchPlan:
    question: str
    search_terms: str = ""
    target_types: list[str] = field(default_factory=list)
    entity_targets: list[str] = field(default_factory=list)
    hypothetical_clue: str = ""
    follow_up_terms: str = ""
    k1: int = 8
    k2: int = 12


PLAN_PROMPT = """You are the retrieval planner of a detective-novel QA system.
Question: {question}
The knowledge graph contains these notable nodes (name | type | salience | evidence):
{glossary}

Decide how to search the graph for the clues that decide the answer. Return strict JSON only:
{{
  "search_terms": "expanded lexical query (question + grounded terms)",
  "target_types": ["node types to prioritize, e.g. clue_object, location"],
  "entity_targets": ["exact node names the question points to"],
  "hypothetical_clue": "one short paragraph (2-3 sentences) of the kind of clue sentences that would decide this question",
  "follow_up_terms": "extra search terms to use after the first hop (who/what/where details)"
}}"""


def plan_search(client: Any, question: str, store: Any, *, cache_dir: Path | None = None, graph_fp: str = "") -> SearchPlan:
    model_tag = (
        f"{getattr(client, 'model', '')}|{getattr(client, 'thinking', '') or ''}"
        f"|{getattr(client, 'reasoning_effort', '') or ''}"
    )
    key = hashlib.sha1(f"{question}|{model_tag}|{graph_fp}".encode("utf-8")).hexdigest()[:12]
    path = (cache_dir / f"plan_{key}.json") if cache_dir else None
    if path is not None:
        cached = load_json(path)
        if cached:
            cached = dict(cached)
            cached.pop("question", None)
            return SearchPlan(question=question, **cached)
    glossary_nodes = sorted(store.nodes, key=lambda n: (-(n.get("salience", 3) + n.get("degree", 0))))[:60]
    glossary = "\n".join(
        f"- {n['name']} | {n['type']} | salience={n.get('salience', 3)} | {(n.get('evidence') or [''])[0][:60]}"
        for n in glossary_nodes
    )
    payload = client.complete_json(
        "You are an expert retrieval planner.",
        PLAN_PROMPT.format(question=question, glossary=glossary),
        max_tokens=1500,
    )
    plan = SearchPlan(
        question=question,
        search_terms=str(payload.get("search_terms") or ""),
        target_types=[str(t) for t in (payload.get("target_types") or [])],
        entity_targets=[str(t) for t in (payload.get("entity_targets") or [])],
        hypothetical_clue=str(payload.get("hypothetical_clue") or ""),
        follow_up_terms=str(payload.get("follow_up_terms") or ""),
    )
    if path is not None:
        save_json(path, plan.__dict__)
    return plan


def execute(store: Any, plan: SearchPlan) -> tuple[list[str], list[str]]:
    scores = (
        store.index.score(plan.question)
        + 1.2 * store.index.score(plan.search_terms)
        + 0.7 * store.index.score(plan.hypothetical_clue)
        + 0.5 * store.index.score(plan.follow_up_terms)
    )
    name_to_idx = {node["name"]: i for i, node in enumerate(store.nodes)}
    for target in plan.entity_targets:
        idx = name_to_idx.get(target)
        if idx is not None:
            scores[idx] += 3.0
    for i, node in enumerate(store.nodes):
        scores[i] *= TYPE_BOOST.get(node["type"], 1.0)
        scores[i] += 0.4 * max(int(node.get("salience", 3)) - 1, 0)
    order = scores.argsort()[::-1]
    first = [store.nodes[i]["id"] for i in order if scores[i] > 0][: plan.k1]
    first_set = set(first)
    second_scores: dict[str, float] = {}
    for node_id in first:
        for neighbor, edge in store.adj.get(node_id, []):
            if neighbor in first_set:
                continue
            weight = float(edge.get("confidence") or 0.0)
            if edge.get("decoy"):
                weight *= 0.4
            weight *= max(int(edge.get("importance") or 3), 1) / 3.0
            second_scores[neighbor] = max(second_scores.get(neighbor, 0.0), weight)
    second = sorted(second_scores, key=lambda nid: (-second_scores[nid], nid))[: plan.k2]
    return first, second


def top_sentences(sentence_index: BM25Index, question: str, plan: SearchPlan, k: int = 6) -> list[tuple[int, str, float]]:
    query = " ".join([question, plan.search_terms, plan.hypothetical_clue, plan.follow_up_terms])
    scores = sentence_index.score(query)
    order = scores.argsort()[::-1]
    rows = []
    for i in order:
        if scores[i] <= 0:
            break
        rows.append((i, "", float(scores[i])))
        if len(rows) >= k:
            break
    return rows
