"""False-clue / false-testimony verification: judge candidate claims against retrieved evidence."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from ..cache import load_json, save_json

VERIFY_PROMPT = """Detective-novel evidence verification.
Question: {question}
Claim: "{candidate}" is {role}.

Evidence sentences:
{evidence}

Graph clues:
{clues}

Important: a confession may be FALSE (e.g. someone confesses to protect another person), and a clue
may be a decoy / false trail (e.g. "left the front door half-open to create the illusion").
Judge the claim against the FULL evidence and point out contradictions.
Return strict JSON only: {{"verdict": "support|refute|unknown", "confidence": 0.0-1.0, "reason": "..."}}"""

GENERIC_PERSONS = {"the killer", "the murderer", "the victim", "the woman", "another man", "young man", "husband", "inspector", "the girl", "the boy"}
CONFESSION_KEYWORDS = re.compile(
    r"\b(confess|confessed|confession|protect|said she was the killer|said he was the killer|guilty|in fact|actually|wasn't her|not her)\b",
    re.IGNORECASE,
)


def role_for_question(question: str) -> str:
    text = str(question or "")
    match = re.search(r"killed\s+(.+?)[?？]", text)
    if match:
        return f"the killer of {match.group(1).strip()}"
    if "accomplice" in text.lower():
        return "the accomplice of the killer"
    return "the correct answer"


def confession_sentences(kept: list[dict[str, Any]], k: int = 4) -> list[str]:
    hits = [row for row in kept if CONFESSION_KEYWORDS.search(row["text"])]
    hits.sort(key=lambda r: r["seq"])
    if len(hits) <= k:
        return [row["text"] for row in hits]
    half = k // 2
    return [row["text"] for row in hits[:half]] + [row["text"] for row in hits[-(k - half):]]


def candidate_names(
    store: Any,
    first: list[str],
    second: list[str],
    targets: list[str],
    victim_name: str = "",
    cap: int = 6,
) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    by_name = {node["name"].lower(): node["id"] for node in store.nodes}
    victim_id = by_name.get(str(victim_name or "").lower())
    if victim_id is not None:
        for neighbor, _ in store.adj.get(victim_id, []):
            node = store.by_id.get(neighbor)
            if node is not None and node["type"] == "person" and node["name"].lower() not in GENERIC_PERSONS:
                name = node["name"]
                if name.lower() != str(victim_name or "").lower() and name not in seen:
                    seen.add(name)
                    names.append(name)
    for node_id in first + second:
        node = store.by_id.get(node_id)
        if node is None or node["type"] != "person":
            continue
        name = node["name"]
        if name.lower() in GENERIC_PERSONS or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= cap:
            return names[:cap]
    for target in targets:
        if target not in seen and target.lower() in by_name:
            names.append(target)
            seen.add(target)
    return names[:cap]


def verify_candidates(
    client: Any,
    question: str,
    candidates: list[str],
    evidence_sentences: list[str],
    clue_lines: list[str],
    cache_dir: Path,
    cache_key: str,
) -> list[dict[str, Any]]:
    role = role_for_question(question)
    verdicts: list[dict[str, Any]] = []
    for candidate in candidates:
        key = hashlib.sha1(f"{cache_key}|{candidate}".encode("utf-8")).hexdigest()[:12]
        path = cache_dir / f"v_{key}.json"
        cached = load_json(path)
        if cached:
            verdicts.append({**cached, "candidate": candidate})
            continue
        payload = client.complete_json(
            "You are an evidence-verification specialist.",
            VERIFY_PROMPT.format(
                question=question,
                candidate=candidate,
                role=role,
                evidence="\n".join(f"- {s}" for s in evidence_sentences[:8]),
                clues="\n".join(f"- {c}" for c in clue_lines[:8]),
            ),
            max_tokens=600,
        )
        row = {
            "candidate": candidate,
            "verdict": str(payload.get("verdict") or "unknown") if isinstance(payload, dict) else "unknown",
            "confidence": float(payload.get("confidence") or 0.0) if isinstance(payload, dict) else 0.0,
            "reason": str(payload.get("reason") or "") if isinstance(payload, dict) else "",
        }
        save_json(path, row)
        verdicts.append(row)
    return verdicts


def best_candidate(verdicts: list[dict[str, Any]], threshold: float = 0.3) -> str | None:
    best_name: str | None = None
    best_score = 0.0
    for verdict in verdicts:
        confidence = float(verdict.get("confidence") or 0.0)
        score = {
            "support": confidence,
            "refute": -confidence,
            "unknown": 0.0,
        }.get(str(verdict.get("verdict") or "unknown"), 0.0)
        if score > best_score:
            best_score = score
            best_name = verdict["candidate"]
    return best_name if best_score > threshold else None
