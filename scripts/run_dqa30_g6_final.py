"""Final strict entry point for the G6 graph-expansion experiment."""

from __future__ import annotations

import json

import run_dqa30_g6_graph_expansion as g6
import run_dqa30_g6_strict  # noqa: F401  # installs strict response parsing


_valid_cache = g6.valid_cache


def _strict_valid_cache(path, graph_sha: str, source_hash: str) -> bool:
    if not _valid_cache(path, graph_sha, source_hash):
        return False
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return row.get("selected_letter") in set(g6.LETTERS)
    except Exception:
        return False


g6.valid_cache = _strict_valid_cache


if __name__ == "__main__":
    g6.main()
