"""Lightweight iterative KG-constrained verification (adapted from the group's iterative controller).

Per candidate: propose an obligation -> LLM verifies against evidence -> update state
(accepted / rejected / unknown / revised) -> revise or stop -> score -> rank.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..cache import load_json, save_json

VERIFY_PROMPT = """Detective-novel evidence verification.
Question: {question}
Claim: "{candidate}" is the correct answer.
Obligation: {obligation}
  - evidence_support: is there concrete evidence supporting the claim?
  - counterevidence: is there concrete evidence contradicting or refuting the claim?
  - decoy_check: is the supporting evidence a decoy / false trail?

Evidence sentences:
{evidence}

Graph clues:
{clues}

Remember: a confession may be false (protecting someone) and a clue may be a decoy.
Return strict JSON only: {{"verdict": "supported|violated|unknown", "confidence": 0.0-1.0, "reason": "..."}}"""

OBLIGATIONS_WHO = ["evidence_support", "counterevidence"]
OBLIGATIONS_OTHER = ["evidence_support"]


def verdict(client: Any, cache_dir: Path, key: str, prompt: str) -> dict[str, Any]:
    safe_key = hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:16]
    path = (cache_dir / f"iv_{safe_key}.json") if cache_dir is not None else None
    if path is not None:
        cached = load_json(path)
        if cached:
            return cached
    payload = client.complete_json(
        "You are an evidence-verification specialist.",
        prompt,
        max_tokens=500,
    )
    row = {
        "verdict": str(payload.get("verdict") or "unknown") if isinstance(payload, dict) else "unknown",
        "confidence": float(payload.get("confidence") or 0.0) if isinstance(payload, dict) else 0.0,
        "reason": str(payload.get("reason") or "") if isinstance(payload, dict) else "",
    }
    if path is not None:
        save_json(path, row)
    return row


def run_iterative(
    client: Any,
    *,
    question: str,
    candidates: list[dict[str, Any]],
    evidence_by_candidate: dict[str, list[str]],
    clues: list[str],
    cache_dir: Path,
    key: str,
    max_obligations: int = 2,
    who: bool = False,
) -> list[dict[str, Any]]:
    obligations = OBLIGATIONS_WHO if who else OBLIGATIONS_OTHER
    results: list[dict[str, Any]] = []
    for cand in candidates:
        name = str(cand.get("name") or cand.get("text") or "")
        evidence = evidence_by_candidate.get(name, [])
        state = "unknown"
        verdicts: list[dict[str, Any]] = []
        for obligation in obligations[:max_obligations]:
            v = verdict(
                client,
                cache_dir,
                f"{key}|{name}|{obligation}",
                VERIFY_PROMPT.format(
                    question=question,
                    candidate=name,
                    obligation=obligation,
                    evidence="\n".join(f"- {s}" for s in evidence[:6]),
                    clues="\n".join(f"- {c}" for c in clues[:6]),
                ),
            )
            verdicts.append({**v, "obligation": obligation})
            if v["verdict"] == "supported" and float(v["confidence"]) >= 0.6:
                state = "accepted"
                break
            if v["verdict"] == "violated" and float(v["confidence"]) >= 0.6:
                state = "rejected"
                break
        score = sum(
            (float(v["confidence"]) if v["verdict"] == "supported" else -float(v["confidence"]) if v["verdict"] == "violated" else 0.0)
            for v in verdicts
        )
        if state == "accepted":
            score += 0.5
        if state == "rejected":
            score -= 0.5
        results.append({"candidate": name, "state": state, "score": round(score, 3), "verdicts": verdicts})
    results.sort(key=lambda r: -r["score"])
    return results
