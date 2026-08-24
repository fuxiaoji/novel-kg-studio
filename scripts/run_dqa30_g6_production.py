"""Production entry point for the G6 graph-expansion experiment.

This wrapper keeps the experimental core unchanged while enforcing a strict
four-choice protocol, robust JSON parsing, and non-empty cache validation.
"""

from __future__ import annotations

import json
import re

import run_dqa30_g6_graph_expansion as g6
from native_ollama_client import NativeOllamaNoThinkClient
from novel_kg_studio.llm import extract_json


_base_normalize = g6.normalize_letter


def _normalize_letter(value):
    return _base_normalize(value) or "?"


def _valid_cache(path, graph_sha: str, source_hash: str) -> bool:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return (
            row.get("version") == g6.VERSION
            and row.get("graph_sha256") == graph_sha
            and row.get("source_hash") == source_hash
            and row.get("selected_letter") in set(g6.LETTERS)
        )
    except Exception:
        return False


def _parse_object(raw: str):
    try:
        parsed = extract_json(raw)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    match = re.search(r"(?:selected_letter|answer)\s*[:=\"']+\s*([ABCD])\b", raw, re.I)
    return {"selected_letter": match.group(1).upper(), "raw": raw} if match else {"raw": raw}


def _complete_json(self, system: str, user: str, *, max_tokens: int = 800):
    suffix = " Return exactly one JSON object. You must select exactly one of A, B, C, or D."
    first_raw = self.complete(system + suffix, user, max_tokens=max_tokens)
    parsed = _parse_object(first_raw)
    if _normalize_letter(parsed.get("selected_letter")) in set(g6.LETTERS):
        return parsed
    retry_raw = self.complete(
        system + suffix + " Evidence may be incomplete; choose the closest supported option and never abstain.",
        user,
        max_tokens=max_tokens,
    )
    retried = _parse_object(retry_raw)
    retried["forced_choice_after_abstention"] = True
    retried["first_raw"] = first_raw
    return retried


g6.normalize_letter = _normalize_letter
g6.valid_cache = _valid_cache
NativeOllamaNoThinkClient.complete_json = _complete_json


if __name__ == "__main__":
    g6.main()
