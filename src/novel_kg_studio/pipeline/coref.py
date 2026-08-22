"""Coreference repair stage: resolve pronouns in relation evidence, re-attach edges."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from ..cache import load_json, save_json
from ..schema import norm_text

PRONOUN_TOKENS = re.compile(r"\b(he|him|his|she|her|i|me|my|we|us|our|they|them|their)\b", re.IGNORECASE)
PRONOUN_NAMES = {"he", "him", "his", "she", "her", "i", "me", "my", "we", "us", "our", "they", "them", "their"}
GENERIC_PATTERN = re.compile(r"^the (man|woman|girl|boy|killer|murderer|victim|narrator|doctor|servant|patient|body)$")


def _generic(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    return lowered in PRONOUN_NAMES or bool(GENERIC_PATTERN.match(lowered))


def _parse_seq_key(value: Any) -> int | None:
    """Parse plain sequence keys ("385") and prompt-style keys ("id=385")."""
    match = re.fullmatch(r"\s*(?:id\s*=\s*)?(\d+)\s*", str(value), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _normalize_mapping(payload: Any, valid_seqs: set[int]) -> dict[int, str | None]:
    mapping: dict[int, str | None] = {}
    for key, value in (payload.items() if isinstance(payload, dict) else {}):
        seq = _parse_seq_key(key)
        if seq is None or seq not in valid_seqs:
            continue
        mapping[seq] = str(value) if value not in (None, "", "null") else None
    return mapping

def _build_prompt(batch: list[int], by_seq: dict[int, dict], candidate_names: list[str], window: int) -> str:
    blocks = []
    for seq in batch:
        rows = [by_seq[s] for s in range(max(0, seq - window), min(len(by_seq), seq + window + 1))]
        lines = "\n".join(f"[{row['seq']}] {row['text']}" for row in rows)
        blocks.append(f"### id={seq}\n{lines}\n")
    candidates = "\n".join(f"- {name}" for name in candidate_names)
    return (
        "You resolve pronoun antecedents in a detective novel. Candidate characters:\n"
        + candidates
        + "\n\nFor each block, the pronoun in the TARGET line (the middle line) refers to which "
        "character? Answer with the exact candidate name, or null if unclear.\n\n"
        + "\n".join(blocks)
        + "\n\nReturn ONE strict JSON object mapping each id to a name or null, "
        'e.g. {"1840": "Bella DuVain", "1843": null}. Do not add extra fields or lines.'
    )


def repair_graph(
    graph: dict[str, Any],
    kept_rows: list[dict[str, Any]],
    client: Any,
    out_dir: Path,
    *,
    batch_size: int = 8,
    window: int = 1,
    resume: bool = True,
    log: Callable[[str], None] = print,
) -> tuple[dict[str, Any], dict[str, Any]]:
    nodes, edges = graph["nodes"], graph["edges"]
    by_id = {n["id"]: n for n in nodes}
    by_name = {norm_text(n["name"]): n["id"] for n in nodes}
    kept_norm = [(row["seq"], norm_text(row["text"])) for row in kept_rows]

    def locate(evidence: str) -> int | None:
        head = norm_text(evidence)[:40]
        if not head:
            return None
        for seq, row_norm in kept_norm:
            if head in row_norm:
                return seq
        return None

    pronoun_edges = [
        e
        for e in edges
        if PRONOUN_TOKENS.search(e.get("evidence", ""))
        and (_generic(by_id[e["source"]]["name"]) or _generic(by_id[e["target"]]["name"]))
    ]
    tasks: dict[int, list[dict]] = {}
    edge_task: dict[str, int] = {}
    for edge in pronoun_edges:
        seq = locate(edge.get("evidence", ""))
        if seq is None:
            continue
        tasks.setdefault(seq, []).append(edge)
        edge_task[edge["id"]] = seq

    candidate_names = [
        n["name"]
        for n in sorted(
            (node for node in nodes if node["type"] == "person" and not _generic(node["name"])),
            key=lambda node: -node["degree"],
        )[:60]
    ]
    by_seq = {row["seq"]: row for row in kept_rows}
    ordered_seqs = sorted(tasks)
    batches = [ordered_seqs[i : i + batch_size] for i in range(0, len(ordered_seqs), batch_size)]

    antecedents: dict[int, str | None] = {}
    for batch in batches:
        fingerprint = hashlib.sha1(",".join(str(s) for s in batch).encode("utf-8")).hexdigest()[:12]
        cache_file = out_dir / "coref" / f"b_{fingerprint}.json"
        cached = load_json(cache_file) if resume else None
        if cached:
            antecedents.update(_normalize_mapping(cached, set(batch)))
            continue
        payload = client.complete_json(
            "You are an expert in coreference resolution for long novels.",
            _build_prompt(batch, by_seq, candidate_names, window),
            max_tokens=2500,
        )
        mapping = _normalize_mapping(payload, set(batch))
        save_json(cache_file, mapping)
        antecedents.update(mapping)
    log(f"[coref] resolved {sum(1 for v in antecedents.values() if v)}/{len(antecedents)} sentences")

    moved = 0
    unmoved = 0
    examples: list[dict] = []
    modified: dict[str, dict] = {}
    for edge in pronoun_edges:
        seq = edge_task.get(edge["id"])
        if seq is None:
            unmoved += 1
            continue
        antecedent = antecedents.get(seq)
        target_id = by_name.get(norm_text(antecedent)) if antecedent else None
        if target_id is None:
            unmoved += 1
            continue
        before = f"{by_id[edge['source']]['name']} --{edge['type']}--> {by_id[edge['target']]['name']}"
        if _generic(by_id[edge["source"]]["name"]):
            edge["source"] = target_id
            moved += 1
        elif _generic(by_id[edge["target"]]["name"]):
            edge["target"] = target_id
            moved += 1
        else:
            unmoved += 1
            continue
        after = f"{by_id[edge['source']]['name']} --{edge['type']}--> {by_id[edge['target']]['name']}"
        modified[edge["id"]] = edge
        if len(examples) < 10:
            examples.append(
                {"before": before, "after": after, "evidence": edge["evidence"][:110], "antecedent": antecedent}
            )

    repaired_edges: list[dict] = []
    seen: set[tuple] = set()
    for edge in (modified.get(e["id"], e) for e in edges):
        key = (edge["source"], edge["target"], edge["type"], norm_text(edge.get("evidence", "")))
        if key in seen:
            continue
        seen.add(key)
        repaired_edges.append(edge)

    repaired = {**graph, "edges": repaired_edges}
    stats = {
        "pronoun_edges": len(pronoun_edges),
        "contextualized_edges": len(edge_task),
        "resolved_sentences": sum(1 for v in antecedents.values() if v),
        "moved_edges": moved,
        "unmoved_edges": unmoved,
        "deduplicated_edges": len(edges) - len(repaired_edges),
        "examples": examples,
    }
    save_json(out_dir / "coref_repair.json", stats)
    log(f"[coref] moved_edges={moved} unmoved={unmoved} edges {len(edges)} -> {len(repaired_edges)}")
    return repaired, stats

