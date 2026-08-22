"""Read-only option-aware retrieval methods for the option-C ablation study.

This module never builds or writes a graph.  It receives an existing graph and
novel text, then returns a structured multiple-choice answer plus an auditable
trace.  The four methods deliberately keep different retrieval behaviours so
their effects can be measured independently.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

from novel_kg_studio.chunking import TextChunk, chunk_text, find_span
from novel_kg_studio.store import GraphStore
from novel_kg_studio.store.bm25 import BM25Index

LETTERS = "ABCD"
NEGATIVE_RE = re.compile(r"\b(not|incorrect|false|except|never|least likely|ruled out)\b", re.I)
IDENTITY_RE = re.compile(r"\b(identity|real identity|impersonat|disguise|who is|who was|whose body)\b", re.I)


def strict_visible_text(novel_text: str, mask_char: int | None) -> str:
    """Apply a strict character mask; chunks can never cross the boundary."""
    if not isinstance(mask_char, int) or mask_char <= 0:
        return novel_text
    return novel_text[: min(mask_char, len(novel_text))]


def normalize_letter(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    patterns = [
        r"(?i)\bselected_letter\b\s*[:=]\s*[\"']?([A-D])",
        r"(?i)\b(?:final\s+)?answer\s*[:=\-]?\s*\(?([A-D])\)?",
        r"(?i)\bcorrect\s+(?:answer|option)\s+(?:is\s+)?\(?([A-D])\)?",
        r"^\s*\(?([A-D])\)?(?:[.\):\s]|$)",
    ]
    for pattern in patterns:
        hits = re.findall(pattern, text)
        if hits:
            return hits[-1].upper()
    return None


def question_type(question: str) -> str:
    q = question.lower()
    if NEGATIVE_RE.search(q):
        return "negative_check"
    if re.search(r"\b(who killed|killer|murderer|perpetrator|mastermind|instigator)\b", q):
        return "killer"
    if IDENTITY_RE.search(q):
        return "identity"
    if re.search(r"\b(why|reason|motive|purpose|cause of|caused)\b", q):
        return "motive_reason"
    if re.search(r"\b(how many|number of|symbol|letters?\s+[A-Z0-9])\b", question, re.I):
        return "quantity_symbol"
    if re.search(r"\b(where|location|room|place)\b", q):
        return "location"
    if re.search(r"\b(when|before|after|time|order)\b", q):
        return "time_order"
    if re.search(r"\b(how did|method|means|weapon|through)\b", q):
        return "method"
    return "clue_fact"


def _node_doc(store: GraphStore, node: dict[str, Any]) -> str:
    rels = []
    for neighbor, edge in store.adj.get(node["id"], [])[:12]:
        target = store.by_id.get(neighbor, {})
        rels.append(f"{edge.get('type', '')} {target.get('name', '')} {edge.get('evidence', '')}")
    return " ".join(
        [
            str(node.get("name", "")),
            " ".join(str(a) for a in node.get("aliases", [])),
            str(node.get("type", "")),
            str(node.get("description", "")),
            " ".join(str(e) for e in node.get("evidence", [])[:5]),
            " ".join(rels),
        ]
    )


@dataclass
class EvidenceContext:
    chunks: list[TextChunk]
    chunk_index: BM25Index
    store: GraphStore
    node_docs: list[str]
    node_index: BM25Index
    novel_text: str
    mask_policy: str

    @classmethod
    def build(cls, graph: dict[str, Any], novel_text: str, mask_char: int | None) -> "EvidenceContext":
        visible = strict_visible_text(novel_text, mask_char)
        chunks = chunk_text(visible, size=1500, overlap=100, prefix="c")
        strict_masked = isinstance(mask_char, int) and mask_char > 0
        if strict_masked:
            # The graph was built from the full novel.  A strict masked run may
            # expose only facts whose verbatim evidence occurs before the mask.
            # Full descriptions, aliases, attributes, and unsupported edges can
            # encode the reveal even when raw source chunks are hidden.
            nodes = []
            kept_ids = set()
            visible_ratio = len(visible) / max(len(novel_text), 1)
            for original in graph.get("nodes", []):
                visible_evidence = [
                    str(ev)
                    for ev in original.get("evidence", [])
                    if find_span(visible, str(ev))[0] >= 0
                ]
                try:
                    appears_before_mask = float(original.get("text_pos", 1.0)) <= visible_ratio
                except (TypeError, ValueError):
                    appears_before_mask = False
                if not visible_evidence and not appears_before_mask:
                    continue
                node = dict(original)
                node["evidence"] = visible_evidence
                # A full-graph description may synthesize a later reveal.  When
                # no verbatim pre-mask evidence is available, retain only the
                # existence/name/type of a node known to occur before the cut.
                node["description"] = " ".join(visible_evidence[:3])
                node["aliases"] = []
                node["attributes"] = {}
                nodes.append(node)
                kept_ids.add(node["id"])
            edges = []
            for original in graph.get("edges", []):
                evidence = str(original.get("evidence", ""))
                if original.get("source") not in kept_ids or original.get("target") not in kept_ids:
                    continue
                if not evidence or find_span(visible, evidence)[0] < 0:
                    continue
                edges.append(dict(original))
            store = GraphStore(nodes, edges)
            mask_policy = "strict-source-filtered-graph-v1"
        else:
            store = GraphStore(graph.get("nodes", []), graph.get("edges", []))
            mask_policy = "unmasked-full-graph"
        node_docs = [_node_doc(store, node) for node in store.nodes]
        return cls(
            chunks=chunks,
            chunk_index=BM25Index([c.text for c in chunks]),
            store=store,
            node_docs=node_docs,
            node_index=BM25Index(node_docs),
            novel_text=visible,
            mask_policy=mask_policy,
        )

    def top_chunks(self, query: str, limit: int = 4) -> list[int]:
        scores = self.chunk_index.score(query)
        order = np.argsort(scores)[::-1]
        return [int(i) for i in order if scores[i] > 0][:limit]

    def top_nodes(self, query: str, limit: int = 4) -> list[int]:
        scores = self.node_index.score(query)
        order = np.argsort(scores)[::-1]
        return [int(i) for i in order if scores[i] > 0][:limit]

    def chunk_for_span(self, span: str) -> int | None:
        start, _ = find_span(self.novel_text, span)
        if start < 0:
            return None
        candidates = [i for i, c in enumerate(self.chunks) if c.start <= start < c.end]
        return candidates[-1] if candidates else None


def rrf_option_evidence(
    ctx: EvidenceContext,
    question: str,
    choices: list[str],
    *,
    chunk_limit: int = 10,
    node_limit: int = 16,
) -> dict[str, Any]:
    """Retrieve independently per option and fuse ranks without score-scale leakage."""
    chunk_rrf: defaultdict[int, float] = defaultdict(float)
    node_rrf: defaultdict[int, float] = defaultdict(float)
    by_option: dict[str, dict[str, list[str]]] = {}
    queries = [("Q", question)] + [(LETTERS[i], f"{question} {choice}") for i, choice in enumerate(choices[:4])]
    for label, query in queries:
        cids = ctx.top_chunks(query, 4)
        nids = ctx.top_nodes(query, 5)
        by_option[label] = {
            "chunks": [ctx.chunks[i].id for i in cids],
            "nodes": [ctx.store.nodes[i]["id"] for i in nids],
        }
        for rank, idx in enumerate(cids):
            chunk_rrf[idx] += 1.0 / (60 + rank)
        for rank, idx in enumerate(nids):
            node_rrf[idx] += 1.0 / (60 + rank)
            node = ctx.store.nodes[idx]
            for ev in node.get("evidence", [])[:3]:
                ci = ctx.chunk_for_span(str(ev))
                if ci is not None:
                    chunk_rrf[ci] += 1.0 / (65 + rank)
    ranked_chunks = sorted(chunk_rrf, key=lambda i: (-chunk_rrf[i], i))[:chunk_limit]
    ranked_nodes = sorted(node_rrf, key=lambda i: (-node_rrf[i], ctx.store.nodes[i]["id"]))[:node_limit]
    return {
        "chunks": [
            {"id": ctx.chunks[i].id, "start": ctx.chunks[i].start, "end": ctx.chunks[i].end, "text": ctx.chunks[i].text}
            for i in ranked_chunks
        ],
        "nodes": [
            {
                "id": ctx.store.nodes[i]["id"],
                "name": ctx.store.nodes[i].get("name", ""),
                "type": ctx.store.nodes[i].get("type", ""),
                "description": ctx.store.nodes[i].get("description", ""),
                "evidence": ctx.store.nodes[i].get("evidence", [])[:3],
            }
            for i in ranked_nodes
        ],
        "by_option": by_option,
    }


def _options_text(choices: list[str]) -> str:
    return "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(choices[:4]))


def _evidence_text(package: dict[str, Any], max_chunks: int = 10) -> str:
    nodes = "\n".join(
        f"[{n['id']}] {n['name']} ({n['type']}): {n['description']} | {' | '.join(n['evidence'])}"
        for n in package["nodes"][:16]
    )
    chunks = "\n\n".join(f"[{c['id']} @ {c['start']}]\n{c['text']}" for c in package["chunks"][:max_chunks])
    return f"GRAPH NODES\n{nodes}\n\nSOURCE CHUNKS\n{chunks}"


def _answer_schema() -> str:
    return (
        '{"selected_letter":"A|B|C|D","confidence":"high|medium|low",'
        '"evidence":{"A":{"support":["c_1"],"contradict":["c_2"],"decoy":[]},'
        '"B":{"support":[],"contradict":[],"decoy":[]},'
        '"C":{"support":[],"contradict":[],"decoy":[]},'
        '"D":{"support":[],"contradict":[],"decoy":[]}},'
        '"reason":"brief evidence-linked reason","needs_more":false,"followup_queries":[]}'
    )


def normalize_payload(payload: Any, choices: list[str], valid_ids: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    letter = normalize_letter(payload.get("selected_letter"))
    confidence = str(payload.get("confidence", "low")).lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"
    evidence: dict[str, dict[str, list[str]]] = {}
    raw_ev = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    for letter_key in LETTERS:
        row = raw_ev.get(letter_key, {}) if isinstance(raw_ev.get(letter_key), dict) else {}
        evidence[letter_key] = {}
        for kind in ("support", "contradict", "decoy"):
            vals = row.get(kind, []) if isinstance(row.get(kind), list) else []
            evidence[letter_key][kind] = [str(v) for v in vals if str(v) in valid_ids][:4]
    selected_support = evidence.get(letter or "", {}).get("support", [])
    selected_contradict = evidence.get(letter or "", {}).get("contradict", [])
    decisive = bool(letter and (selected_support or selected_contradict))
    if not decisive:
        confidence = "low"
    return {
        "selected_letter": letter,
        "selected_text": choices[LETTERS.index(letter)]
        if isinstance(letter, str) and letter in LETTERS and LETTERS.index(letter) < len(choices)
        else "",
        "confidence": confidence,
        "evidence": evidence,
        "evidence_ids": sorted({x for row in evidence.values() for vals in row.values() for x in vals}),
        "reason": str(payload.get("reason", "")).strip(),
        "needs_more": bool(payload.get("needs_more", False)) or not decisive,
        "followup_queries": [str(q) for q in payload.get("followup_queries", []) if str(q).strip()][:3]
        if isinstance(payload.get("followup_queries"), list)
        else [],
        "decisive": decisive,
    }


def _call_json(client: Any, system: str, prompt: str, max_tokens: int = 2200) -> tuple[Any, str]:
    error = ""
    for _ in range(3):
        try:
            payload = client.complete_json(system, prompt, max_tokens=max_tokens)
            if isinstance(payload, dict):
                return payload, ""
        except Exception as exc:  # external model failures must be resumable
            error = f"{type(exc).__name__}: {exc}"
    return {}, error


def _base_prompt(q: dict[str, Any], package: dict[str, Any]) -> str:
    polarity = "NEGATIVE: prove an option false; missing evidence is NOT contradiction." if NEGATIVE_RE.search(q["question"]) else "POSITIVE"
    return (
        f"Question: {q['question']}\nQuestion mode: {polarity}\n\nOPTIONS\n{_options_text(q['choices'])}\n\n"
        f"{_evidence_text(package)}\n\n"
        "Compare every option. Cite only supplied chunk IDs. A real contradiction must be explicit; absence of support is not contradiction. "
        "Do not use genre conventions or guess from option wording. Return strict JSON only using this schema:\n"
        + _answer_schema()
    )


def method_c1(client: Any, q: dict[str, Any], ctx: EvidenceContext) -> dict[str, Any]:
    package = rrf_option_evidence(ctx, q["question"], q["choices"])
    raw, error = _call_json(client, "You are an evidence-grounded multiple-choice detective QA analyst.", _base_prompt(q, package))
    valid = {c["id"] for c in package["chunks"]}
    result = normalize_payload(raw, q["choices"], valid)
    result.update({"method": "c1_v4_option", "retrieval": package, "raw": raw, "error": error})
    return result


def _node_catalog(ctx: EvidenceContext, q: dict[str, Any], limit: int = 30) -> list[dict[str, Any]]:
    query = q["question"] + " " + " ".join(q["choices"])
    ids = ctx.top_nodes(query, limit)
    return [
        {
            "id": ctx.store.nodes[i]["id"],
            "name": ctx.store.nodes[i].get("name", ""),
            "aliases": ctx.store.nodes[i].get("aliases", []),
            "type": ctx.store.nodes[i].get("type", ""),
        }
        for i in ids
    ]


def _node_evidence(ctx: EvidenceContext, node_id: str) -> list[dict[str, Any]]:
    node = ctx.store.by_id.get(node_id)
    if not node:
        return []
    rows = []
    for ev in node.get("evidence", [])[:5]:
        ci = ctx.chunk_for_span(str(ev))
        if ci is None:
            continue
        c = ctx.chunks[ci]
        rows.append({"id": c.id, "start": c.start, "end": c.end, "text": c.text, "node_id": node_id})
    return rows


def method_c2(client: Any, q: dict[str, Any], ctx: EvidenceContext, max_steps: int = 4) -> dict[str, Any]:
    """v5a-style node inspection with aliases and a hard evidence gate."""
    catalog = _node_catalog(ctx, q)
    evidence: dict[str, dict[str, Any]] = {}
    trace = []
    final: dict[str, Any] = {}
    for step in range(max_steps):
        seen = "\n\n".join(f"[{k} @ {v['start']}]\n{v['text']}" for k, v in evidence.items()) or "(none yet)"
        prompt = (
            f"Question: {q['question']}\n\n{_options_text(q['choices'])}\n\n"
            f"Candidate graph nodes (use exact IDs):\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
            f"Inspected source evidence:\n{seen}\n\n"
            "Choose the next graph nodes to inspect. You may answer only when the selected option has cited source evidence or another option is explicitly contradicted. "
            "Missing evidence is not contradiction. Return strict JSON: "
            '{"inspect_node_ids":["node-id"],"selected_letter":null,"confidence":"low",'
            '"evidence":{"A":{"support":[],"contradict":[],"decoy":[]},"B":{"support":[],"contradict":[],"decoy":[]},'
            '"C":{"support":[],"contradict":[],"decoy":[]},"D":{"support":[],"contradict":[],"decoy":[]}},'
            '"reason":"","needs_more":true,"followup_queries":[]}'
        )
        raw, error = _call_json(client, "You navigate a knowledge graph but must ground decisions in source evidence.", prompt, 1800)
        inspected = []
        for nid in raw.get("inspect_node_ids", [])[:5] if isinstance(raw, dict) and isinstance(raw.get("inspect_node_ids"), list) else []:
            for row in _node_evidence(ctx, str(nid)):
                if row["id"] not in evidence:
                    evidence[row["id"]] = row
                    inspected.append(str(nid))
        norm = normalize_payload(raw, q["choices"], set(evidence))
        trace.append({"step": step, "raw": raw, "inspected": inspected, "visible_evidence_ids": sorted(evidence), "error": error})
        if norm["decisive"] and norm["selected_letter"]:
            final = norm
            break
        if not inspected and step > 0:
            break
    if not final:
        fallback = rrf_option_evidence(ctx, q["question"], q["choices"], chunk_limit=8)
        for row in fallback["chunks"]:
            evidence.setdefault(row["id"], row)
        prompt = _base_prompt(q, {"chunks": list(evidence.values())[:10], "nodes": fallback["nodes"], "by_option": fallback["by_option"]})
        raw, error = _call_json(client, "Make one evidence-grounded decision. Never infer falsehood from missing evidence.", prompt)
        final = normalize_payload(raw, q["choices"], set(evidence))
        trace.append({"step": "fallback", "raw": raw, "visible_evidence_ids": sorted(evidence), "error": error})
    final.update({"method": "c2_v5a_gated", "trace": trace, "retrieval": {"catalog": catalog, "chunks": list(evidence.values())}})
    return final


def _find_nodes(ctx: EvidenceContext, query: str, limit: int = 4) -> list[str]:
    qn = re.sub(r"\s+", " ", query.strip().lower())
    exact, fuzzy = [], []
    for node in ctx.store.nodes:
        names = [str(node.get("name", ""))] + [str(a) for a in node.get("aliases", [])]
        normalized = [re.sub(r"\s+", " ", n.strip().lower()) for n in names]
        if qn in normalized:
            exact.append(node["id"])
        elif qn and any(qn in n or n in qn for n in normalized if n):
            fuzzy.append(node["id"])
    return (exact + fuzzy)[:limit]


def method_c3(client: Any, q: dict[str, Any], ctx: EvidenceContext, max_steps: int = 5) -> dict[str, Any]:
    """Clean v5b: deduplicated 1500-char search, legal relations, ranked memory."""
    package = rrf_option_evidence(ctx, q["question"], q["choices"], chunk_limit=6)
    evidence = {row["id"]: {**row, "score": 2.0} for row in package["chunks"]}
    visited_nodes = {n["id"] for n in package["nodes"][:8]}
    seen_queries: set[str] = set()
    relation_types = sorted({str(e.get("type", "")) for e in ctx.store.edges if e.get("type")})
    trace = []
    final: dict[str, Any] = {}
    stagnant = 0
    for step in range(max_steps):
        ranked_ev = sorted(evidence.values(), key=lambda x: (-float(x.get("score", 0)), x["start"]))[:12]
        frontier = []
        for nid in sorted(visited_nodes):
            for neighbor, edge in ctx.store.adj.get(nid, [])[:12]:
                if neighbor not in visited_nodes:
                    frontier.append({"from": nid, "to": neighbor, "relation": edge.get("type", "")})
        prompt = (
            f"Question: {q['question']}\n\n{_options_text(q['choices'])}\n\n"
            f"Evidence memory:\n" + "\n\n".join(f"[{r['id']}]\n{r['text']}" for r in ranked_ev)
            + f"\n\nLegal relations: {relation_types}\nFrontier edges (use exact IDs): {json.dumps(frontier[:35], ensure_ascii=False)}\n\n"
            "Return the next actions and optional answer as strict JSON. Search queries must target facts that distinguish options. "
            "You may answer only with cited evidence; missing evidence is not contradiction. Schema: "
            '{"lookup_names":[],"search_queries":[],"traverse":[{"from":"id","to":"id","relation":"type"}],'
            '"selected_letter":null,"confidence":"low","evidence":{"A":{"support":[],"contradict":[],"decoy":[]},'
            '"B":{"support":[],"contradict":[],"decoy":[]},"C":{"support":[],"contradict":[],"decoy":[]},'
            '"D":{"support":[],"contradict":[],"decoy":[]}},"reason":"","needs_more":true,"followup_queries":[]}'
        )
        raw, error = _call_json(client, "Navigate graph and source text efficiently; prefer decisive evidence over more evidence.", prompt, 2000)
        added_decisive = 0
        actions = {"lookups": [], "searches": [], "traversals": []}
        for name in raw.get("lookup_names", [])[:4] if isinstance(raw, dict) and isinstance(raw.get("lookup_names"), list) else []:
            ids = _find_nodes(ctx, str(name))
            actions["lookups"].append({"query": str(name), "ids": ids})
            for nid in ids:
                if nid not in visited_nodes:
                    visited_nodes.add(nid)
                for row in _node_evidence(ctx, nid):
                    if row["id"] not in evidence:
                        evidence[row["id"]] = {**row, "score": 3.0}
                        added_decisive += 1
        for query in raw.get("search_queries", [])[:3] if isinstance(raw, dict) and isinstance(raw.get("search_queries"), list) else []:
            query = re.sub(r"\s+", " ", str(query).strip().lower())
            if not query or query in seen_queries:
                continue
            seen_queries.add(query)
            hits = ctx.top_chunks(query, 3)
            actions["searches"].append({"query": query, "ids": [ctx.chunks[i].id for i in hits]})
            for rank, ci in enumerate(hits):
                c = ctx.chunks[ci]
                if c.id not in evidence:
                    evidence[c.id] = {"id": c.id, "start": c.start, "end": c.end, "text": c.text, "score": 4.0 - rank}
                    added_decisive += 1
        for move in raw.get("traverse", [])[:4] if isinstance(raw, dict) and isinstance(raw.get("traverse"), list) else []:
            if not isinstance(move, dict):
                continue
            frm, to, rel = str(move.get("from", "")), str(move.get("to", "")), str(move.get("relation", ""))
            legal = any(nb == to and str(edge.get("type", "")) == rel for nb, edge in ctx.store.adj.get(frm, []))
            actions["traversals"].append({"from": frm, "to": to, "relation": rel, "legal": legal})
            if legal:
                visited_nodes.add(to)
                for row in _node_evidence(ctx, to):
                    if row["id"] not in evidence:
                        evidence[row["id"]] = {**row, "score": 3.5}
                        added_decisive += 1
        norm = normalize_payload(raw, q["choices"], set(evidence))
        trace.append({"step": step, "actions": actions, "raw": raw, "added_distinct_evidence": added_decisive, "visible_evidence_ids": sorted(evidence), "error": error})
        if norm["decisive"] and norm["selected_letter"]:
            final = norm
            break
        stagnant = stagnant + 1 if added_decisive == 0 else 0
        if stagnant >= 2:
            break
    if not final:
        ranked_ev = sorted(evidence.values(), key=lambda x: (-float(x.get("score", 0)), x["start"]))[:12]
        final_package = {"chunks": ranked_ev, "nodes": package["nodes"], "by_option": package["by_option"]}
        raw, error = _call_json(client, "Make one evidence-grounded decision from the ranked memory.", _base_prompt(q, final_package))
        final = normalize_payload(raw, q["choices"], set(evidence))
        trace.append({"step": "final", "raw": raw, "visible_evidence_ids": sorted(evidence), "error": error})
    final.update({"method": "c3_v5b_clean", "trace": trace, "retrieval": {"chunks": list(evidence.values()), "initial": package}})
    return final


def method_c4(client: Any, q: dict[str, Any], ctx: EvidenceContext) -> dict[str, Any]:
    """Grounded v7 with ranked chunks, normalized citations, paragraphs, and C1 fallback."""
    package = rrf_option_evidence(ctx, q["question"], q["choices"], chunk_limit=12)
    valid = {c["id"] for c in package["chunks"]}
    extraction_prompt = (
        f"Question: {q['question']}\n\n{_options_text(q['choices'])}\n\n{_evidence_text(package, 12)}\n\n"
        "For each option identify explicit support, contradiction, and decoy source chunks. Missing evidence is not contradiction. "
        "Cite chunk IDs only and return strict JSON with key evidence using this schema: "
        '{"evidence":{"A":{"support":[],"contradict":[],"decoy":[]},"B":{"support":[],"contradict":[],"decoy":[]},'
        '"C":{"support":[],"contradict":[],"decoy":[]},"D":{"support":[],"contradict":[],"decoy":[]}}}'
    )
    extracted, extraction_error = _call_json(client, "Extract option-specific evidence without deciding the answer.", extraction_prompt, 1800)
    normalized = normalize_payload({"evidence": extracted.get("evidence", {}) if isinstance(extracted, dict) else {}}, q["choices"], valid)
    grounded_count = len(normalized["evidence_ids"])
    fallback_used = grounded_count < 2
    final_package = package
    if fallback_used:
        # C1 already uses option-wise RRF; widen it rather than forcing an unsupported v7 answer.
        final_package = rrf_option_evidence(ctx, q["question"] + " " + " ".join(q["choices"]), q["choices"], chunk_limit=12)
        valid = {c["id"] for c in final_package["chunks"]}
    evidence_table = normalized["evidence"]
    prompt = (
        f"Question: {q['question']}\n\n{_options_text(q['choices'])}\n\n"
        f"Extracted support/contradiction/decoy table:\n{json.dumps(evidence_table, ensure_ascii=False)}\n\n"
        f"Full source paragraphs:\n{_evidence_text(final_package, 12)}\n\n"
        "Decide from explicit evidence. Missing support is not a contradiction. Do not use genre conventions. Return strict JSON:\n"
        + _answer_schema()
    )
    raw, answer_error = _call_json(client, "You are a grounded evidence adjudicator.", prompt)
    result = normalize_payload(raw, q["choices"], valid)
    result.update(
        {
            "method": "c4_v7_grounded",
            "retrieval": final_package,
            "extraction": extracted,
            "grounded_count": grounded_count,
            "fallback_used": fallback_used,
            "raw": raw,
            "error": "; ".join(x for x in (extraction_error, answer_error) if x),
        }
    )
    return result


METHODS = {"c1": method_c1, "c2": method_c2, "c3": method_c3, "c4": method_c4}


def run_method(method: str, client: Any, q: dict[str, Any], graph: dict[str, Any], novel_text: str, mask_char: int | None) -> dict[str, Any]:
    started = time.time()
    ctx = EvidenceContext.build(graph, novel_text, mask_char)
    result = METHODS[method](client, q, ctx)
    result["question_type"] = question_type(q["question"])
    result["masked_at"] = mask_char
    result["mask_policy"] = ctx.mask_policy
    result["elapsed_seconds"] = round(time.time() - started, 3)
    result["prompt_version"] = "c-improvements-v1"
    result["prompt_hash"] = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]
    return result
