from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from ..cache import load_json, save_json
from ..chunking import chunk_lines
from ..schema import EDGE_TYPES, NODE_TYPES

PASS2_SYSTEM = """You are building a knowledge graph from time-ordered detective-novel excerpts.
Each input line is numbered [i] text.

Return strict JSON only:
{
  "entities": [
    {"name":"canonical name","type":"person|location|time_anchor|clue_object|event|evidence_sentence",
     "aliases":["..."] ,"mentions":[{"text":"verbatim","sentence_index":0}]}
  ],
  "relations": [
    {"source":"entity name","target":"entity name",
     "type":"located_at|appears_at|belongs_to|mentions|temporal_sequence|supports|contradicts|related_to|motive|means|opportunity|witnessed_by",
     "evidence":"verbatim span","sentence_index":0,"confidence":0.9}
  ]
}

Rules:
- Every mention text and every relation evidence MUST be a verbatim continuous span from the referenced line.
- Evidence <= 200 chars.
- Max 40 entities and 60 relations per chunk; prefer high-value nodes and edges.
- Names are canonical (e.g. "Hercule Poirot"); aliases capture surface variants.
- time_anchor nodes represent explicit times (e.g. "eight o'clock", "Day 2 morning")."""

PASS2_SYSTEM_V2 = """You are building a rich knowledge graph from time-ordered detective-novel excerpts.
Each input line is numbered [i] text.

Return strict JSON only:
{
  "entities": [
    {"name":"canonical name","type":"person|location|time_anchor|clue_object|event|evidence_sentence",
     "aliases":["..."], "description":"one short sentence describing this entity in the novel",
     "salience":3, "attributes":{"role":"...","status":"..."},
     "mentions":[{"text":"verbatim","sentence_index":0}]}
  ],
  "relations": [
    {"source":"entity name","target":"entity name",
     "type":"located_at|appears_at|belongs_to|mentions|temporal_sequence|supports|contradicts|related_to|motive|means|opportunity|witnessed_by",
     "evidence":"verbatim span","sentence_index":0,"confidence":0.9,
     "decoy":false,"importance":3}
  ]
}

Rules:
- Every mention text and every relation evidence MUST be a verbatim continuous span from the referenced line.
- Evidence <= 200 chars. Max 40 entities and 60 relations per chunk.
- description: one short sentence (<= 90 chars) summarizing who/what it is in the story.
- salience / importance: 1-5, 5 = central to the mystery (victim, murder weapon, decisive clue).
- attributes: 1-3 keys like role, status, location, owner; keep values short.
- decoy: true ONLY when the evidence describes a false trail / illusion / misdirection
  (e.g. "left the front door half-open to create the illusion that the killer had left").
- Resolve pronouns to canonical entity names when the referent is clear within the excerpt;
  do not create "he"/"she" entities.
- time_anchor nodes represent explicit times."""

PASS2_SYSTEM_V3 = """You are building a COMPLETE knowledge graph from time-ordered detective-novel excerpts.
Each input line is numbered [i] text.

Return strict JSON only:
{
  "entities": [
    {"name":"canonical name","type":"person|location|time_anchor|clue_object|event|evidence_sentence",
     "aliases":["..."], "description":"one short sentence", "salience":3,
     "attributes":{"role":"...","status":"..."},
     "mentions":[{"text":"verbatim","sentence_index":0}]}
  ],
  "relations": [
    {"source":"entity name","target":"entity name",
     "type":"located_at|appears_at|belongs_to|mentions|temporal_sequence|supports|contradicts|related_to|motive|means|opportunity|witnessed_by",
     "evidence":"verbatim span","sentence_index":0,"confidence":0.9,
     "decoy":false,"importance":3}
  ]
}

Extraction rules (follow EXHAUSTIVELY, do not skip details):
- EVERY named or clearly referenced character -> person (include minor servants, witnesses, victims, suspects).
- EVERY physical object relevant to the plot -> clue_object (weapons, letters, keys, footprints, doors, windows,
  watches, bottles, clothes, papers, stains, bags, ropes, etc.). Do not omit objects because they seem minor.
- EVERY place mentioned -> location (rooms, streets, buildings, gardens, towns).
- EVERY explicit or clearly inferable time -> time_anchor ("ten o'clock", "the following morning", "Day 2", "last night").
- EVERY action or development -> event (arrivals, departures, arguments, discoveries, crimes, searches, phone calls).
- Testimony, statements and explanations that carry facts -> evidence_sentence.
- Resolve pronouns ("he","she","it","they") to canonical names when the referent is clear; never create pronoun entities.
- description: one short sentence (<= 90 chars) about its role in the story.
- salience / importance: 1-5, 5 = central to the mystery (victim, weapon, decisive clue, alibi).
- attributes: 1-3 short keys (role, status, location, owner, time).
- Every mention text and every relation evidence MUST be a verbatim continuous span from the referenced line.
- Evidence <= 200 chars. Names are canonical; aliases capture surface variants ("Poirot", "M. Poirot").

Relation rules:
- Connect entities that co-occur or interact in the same excerpt with the MOST SPECIFIC relation type:
  located_at (entity at place), appears_at (entity at time), belongs_to (ownership),
  mentions (one entity references another), temporal_sequence (time order),
  supports / contradicts (evidence relations), motive (reason for an action),
  means (instrument or method used), opportunity (chance to act), witnessed_by (who observed it),
  related_to (fallback for any other meaningful link).
- Every relation needs a verbatim evidence span and a sentence_index from one line.
- decoy = true ONLY for false trails / illusions / misdirection (e.g. "the open window made it look like an escape").
- Aim for completeness: 30-60 entities and 40-80 relations per chunk when the text supports it.
- Never invent facts that are not in the excerpt."""

PASS2_SYSTEM_V4 = """You are building a RELATION-CENTERED knowledge graph from time-ordered detective-novel excerpts.
Each input line is numbered [i] text.

Return strict JSON only:
{
  "entities": [
    {"name":"canonical name","type":"person|location|time_anchor|clue_object|event|evidence_sentence",
     "aliases":["..."],"description":"one short sentence","salience":3,
     "attributes":{"role":"..."},
     "mentions":[{"text":"verbatim continuous span","sentence_index":0}]}
  ],
  "relations": [
    {"source":"entity name","target":"entity name",
     "type":"located_at|appears_at|belongs_to|mentions|temporal_sequence|supports|contradicts|related_to|motive|means|opportunity|witnessed_by",
     "evidence":"verbatim continuous span","sentence_index":0,"confidence":0.9,
     "decoy":false,"importance":3}
  ]
}

Hard grounding rules:
- First identify plot-relevant RELATIONS, then emit only entities participating in at least one relation.
- Every relation source and target MUST exactly match an emitted entity name or alias in this response.
- Copy mention text and relation evidence character-for-character from ONE referenced input line.
- NEVER abbreviate evidence with "..." or "…", reorder words, combine lines, or include [i] line labels.
- Prefer a smaller connected graph over a long list of isolated objects, places, actions, or statements.
- Do not create generic mood, prose-description, body-part, or whole-sentence nodes unless essential to a clue.
- Do not create pronoun entities. Resolve a pronoun only when its referent is clear in the excerpt.
- Use evidence_sentence only for testimony or statements that directly support or contradict a mystery claim.
- Aim for 8-24 entities and 12-40 relations when supported. It is valid to return fewer.
- Names should be stable across excerpts; put surface variants in aliases.
- Never invent facts."""


def pass2_fingerprint(kept_spans: list, size: int, variant: str = "v2") -> str:
    digest = hashlib.sha1()
    for seq, span in enumerate(kept_spans):
        digest.update(f"{seq}\t{span.text}\n".encode("utf-8"))
    digest.update(str(size).encode("utf-8"))
    digest.update(f"schema_v2|{variant}".encode("utf-8"))
    return digest.hexdigest()[:12]


def pass2_cache_dir(out_dir: Path, kept_spans: list, size: int, variant: str = "v2") -> Path:
    return out_dir / "pass2" / f"s{size}_{pass2_fingerprint(kept_spans, size, variant)}"


def build_pass2_user(lines: list[tuple[int, str]]) -> str:
    numbered = "\n".join(f"[{i}] {text}" for i, text in lines)
    return f"Time-ordered lines:\n{numbered}"


def _normalize_attributes(value: Any) -> dict[str, Any]:
    """Accept common model deviations without discarding the whole chunk."""
    if isinstance(value, dict):
        return {str(key): val for key, val in value.items()}
    if isinstance(value, list):
        normalized: dict[str, Any] = {}
        for item in value:
            if isinstance(item, dict):
                normalized.update({str(key): val for key, val in item.items()})
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                normalized[str(item[0])] = item[1]
            elif isinstance(item, str) and ":" in item:
                key, val = item.split(":", 1)
                if key.strip():
                    normalized[key.strip()] = val.strip()
        return normalized
    return {}

def parse_pass2_payload_v2(payload: Any, max_entities: int = 40, max_rels: int = 60) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return [], []
    entities: list[dict[str, Any]] = []
    for item in (payload.get("entities") or [])[:max_entities]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        etype = str(item.get("type") or "evidence_sentence").strip()
        if not name or etype not in NODE_TYPES:
            continue
        mentions = []
        for m in (item.get("mentions") or [])[:6]:
            if not isinstance(m, dict):
                continue
            text = str(m.get("text") or "").strip()
            try:
                sentence_index = int(m.get("sentence_index"))
            except (TypeError, ValueError):
                continue
            if text and sentence_index >= 0:
                mentions.append({"text": text, "sentence_index": sentence_index})
        aliases = [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()][:6]
        try:
            salience = max(1, min(5, int(item.get("salience") or 3)))
        except (TypeError, ValueError):
            salience = 3
        entities.append(
            {
                "name": name,
                "type": etype,
                "aliases": aliases,
                "mentions": mentions,
                "description": str(item.get("description") or "")[:120],
                "salience": salience,
                "attributes": _normalize_attributes(item.get("attributes")),
            }
        )
    relations: list[dict[str, Any]] = []
    for item in (payload.get("relations") or [])[:max_rels]:
        if not isinstance(item, dict):
            continue
        rtype = str(item.get("type") or "").strip()
        if rtype not in EDGE_TYPES:
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        try:
            sentence_index = int(item.get("sentence_index"))
        except (TypeError, ValueError):
            sentence_index = -1
        try:
            confidence = float(item.get("confidence") or 0.9)
        except (TypeError, ValueError):
            confidence = 0.9
        if not source or not target or not evidence or sentence_index < 0:
            continue
        try:
            importance = max(1, min(5, int(item.get("importance") or 3)))
        except (TypeError, ValueError):
            importance = 3
        relations.append(
            {
                "source": source,
                "target": target,
                "type": rtype,
                "evidence": evidence[:200],
                "sentence_index": sentence_index,
                "confidence": max(0.0, min(1.0, confidence)),
                "decoy": bool(item.get("decoy", False)),
                "importance": importance,
            }
        )
    return entities, relations


def parse_pass2_payload(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return [], []
    entities: list[dict[str, Any]] = []
    for item in (payload.get("entities") or [])[:40]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        etype = str(item.get("type") or "evidence_sentence").strip()
        if not name or etype not in NODE_TYPES:
            continue
        mentions = []
        for m in (item.get("mentions") or [])[:6]:
            if not isinstance(m, dict):
                continue
            text = str(m.get("text") or "").strip()
            try:
                sentence_index = int(m.get("sentence_index"))
            except (TypeError, ValueError):
                continue
            if text and sentence_index >= 0:
                mentions.append({"text": text, "sentence_index": sentence_index})
        aliases = [str(a).strip() for a in (item.get("aliases") or []) if str(a).strip()][:6]
        entities.append({"name": name, "type": etype, "aliases": aliases, "mentions": mentions})
    relations: list[dict[str, Any]] = []
    for item in (payload.get("relations") or [])[:60]:
        if not isinstance(item, dict):
            continue
        rtype = str(item.get("type") or "").strip()
        if rtype not in EDGE_TYPES:
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        try:
            sentence_index = int(item.get("sentence_index"))
        except (TypeError, ValueError):
            sentence_index = -1
        try:
            confidence = float(item.get("confidence") or 0.9)
        except (TypeError, ValueError):
            confidence = 0.9
        if not source or not target or not evidence or sentence_index < 0:
            continue
        relations.append(
            {
                "source": source,
                "target": target,
                "type": rtype,
                "evidence": evidence[:200],
                "sentence_index": sentence_index,
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )
    return entities, relations


def _process_chunk(
    lines: list[tuple[int, str]],
    client: Any,
    cache_dir: Path,
    resume: bool,
    max_tokens: int,
    chunk_id: str,
    prompt: str = PASS2_SYSTEM_V2,
    max_entities: int = 40,
    max_rels: int = 60,
) -> dict[str, Any]:
    path = cache_dir / f"{chunk_id}.json"
    if resume:
        cached = load_json(path)
        if cached is not None and not cached.get("error"):
            return cached
    try:
        payload = client.complete_json(prompt, build_pass2_user(lines), max_tokens=max_tokens)
        entities, relations = parse_pass2_payload_v2(payload, max_entities=max_entities, max_rels=max_rels)
        error = ""
    except Exception as exc:
        entities, relations = [], []
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    record = {
        "chunk_id": chunk_id,
        "line_indices": [i for i, _ in lines],
        "entities": entities,
        "relations": relations,
        "error": error,
    }
    save_json(path, record)
    return record


def run_pass2(
    kept_spans: list,
    *,
    config: dict[str, Any],
    client: Any,
    out_dir: Path,
    resume: bool = True,
    workers: int = 8,
    max_chunks: int | None = None,
    log: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    chunk_cfg = config.get("chunking") or {}
    size = int(chunk_cfg.get("size") or 1500)
    lines = [(seq, span.text) for seq, span in enumerate(kept_spans)]
    numbered = [f"[{i}] {text}" for i, text in lines]
    packed = chunk_lines(numbered, size=size, prefix="pass2")
    if max_chunks is not None:
        packed = packed[:max_chunks]
    max_tokens = int((config.get("model") or {}).get("max_tokens_pass2") or 4000)
    prompt_ver = str(config.get("pass2_prompt") or "v2")
    cache_variant = str(config.get("pass2_cache_variant") or prompt_ver)
    cache_dir = pass2_cache_dir(out_dir, kept_spans, size, cache_variant)
    if prompt_ver == "v4":
        prompt, max_entities, max_rels = PASS2_SYSTEM_V4, 24, 40
    elif prompt_ver == "v3":
        prompt, max_entities, max_rels = PASS2_SYSTEM_V3, 60, 80
    else:
        prompt, max_entities, max_rels = PASS2_SYSTEM_V2, 40, 60

    log(f"[pass2] chunks={len(packed)} workers={workers} resume={resume}")
    records: list[dict[str, Any]] = [None] * len(packed)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        futures = {}
        for idx, (_, line_indices) in enumerate(packed):
            sub_lines = [(i, lines[i][1]) for i in line_indices]
            futures[pool.submit(_process_chunk, sub_lines, client, cache_dir, resume, max_tokens, f"pass2_{idx}", prompt, max_entities, max_rels)] = idx
        done = 0
        for future in as_completed(futures):
            idx = futures[future]
            record = future.result()
            # Preserve the provenance of every model output. Some local models
            # occasionally emit chunk-local sentence indexes even though the
            # prompt displays global indexes. Merge may use this allow-list to
            # relocate verbatim evidence, but never text outside this chunk.
            record["line_indices"] = list(packed[idx][1])
            records[idx] = record
            done += 1
            if done % 20 == 0 or done == len(packed):
                log(f"[pass2] {done}/{len(packed)} chunks")
    return [r for r in records if r]
