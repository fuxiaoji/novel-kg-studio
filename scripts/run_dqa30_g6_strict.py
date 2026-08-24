"""Strict compatibility entry point for the G6 graph-expansion experiment."""

from __future__ import annotations

import json
import re

import run_dqa30_g6_graph_expansion as g6
from native_ollama_client import NativeOllamaNoThinkClient
from novel_kg_studio.llm import extract_json


_normalize_letter = g6.normalize_letter
g6.normalize_letter = lambda value: _normalize_letter(value) or "?"


def _strict_complete_json(self, system: str, user: str, *, max_tokens: int = 800):
    raw = self.complete(
        system + " Return exactly one JSON object beginning with { and ending with }.",
        user,
        max_tokens=max_tokens,
    )
    try:
        parsed = extract_json(raw)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    match = re.search(r"(?:selected_letter|answer)\s*[:=\"']+\s*([ABCD])\b", raw, re.I)
    if match:
        return {"selected_letter": match.group(1).upper(), "raw": raw}
    return {"raw": raw}


NativeOllamaNoThinkClient.complete_json = _strict_complete_json


if __name__ == "__main__":
    g6.main()
