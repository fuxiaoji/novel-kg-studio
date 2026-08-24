"""Compatibility entry point for the G6 experiment runner."""

from __future__ import annotations

import run_dqa30_g6_graph_expansion as g6


_normalize_letter = g6.normalize_letter
g6.normalize_letter = lambda value: _normalize_letter(value) or ""


if __name__ == "__main__":
    g6.main()
