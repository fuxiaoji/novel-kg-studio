"""Robust forced-choice entry point for G6, preserving abstention diagnostics."""

from __future__ import annotations

import re

import run_dqa30_g6_graph_expansion as g6
import run_dqa30_g6_final  # noqa: F401  # installs strict cache and response handling
from native_ollama_client import NativeOllamaNoThinkClient
from novel_kg_studio.llm import extract_json


def _parse_object(raw: str):
    try:
        parsed = extract_json(raw)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    match = re.search(r"(?:selected_letter|answer)\s*[:=\"']+\s*([ABCD])\b", raw, re.I)
    return {"selected_letter": match.group(1).upper(), "raw": raw} if match else {"raw": raw}


def _robust_complete_json(self, system: str, user: str, *, max_tokens: int = 800):
    suffix = " Return exactly one JSON object. You must select exactly one of A, B, C, or D."
    raw = self.complete(system + suffix, user, max_tokens=max_tokens)
    parsed = _parse_object(raw)
    if g6.normalize_letter(parsed.get("selected_letter")) in set(g6.LETTERS):
        return parsed
    raw_retry = self.complete(
        system + suffix + " Evidence may be incomplete; choose the closest supported option and never abstain.",
        user,
        max_tokens=max_tokens,
    )
    retried = _parse_object(raw_retry)
    retried["forced_choice_after_abstention"] = True
    retried["first_raw"] = raw
    return retried


NativeOllamaNoThinkClient.complete_json = _robust_complete_json


if __name__ == "__main__":
    g6.main()
