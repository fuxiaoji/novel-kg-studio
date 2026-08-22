from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from c8_graph_passage import NormalizedLocator, _select_diverse


def test_normalized_locator_returns_original_offset_after_whitespace_collapse() -> None:
    text = "alpha\n\n\n beta\r\n gamma target phrase omega"
    locator = NormalizedLocator(text)
    assert locator.find("target   phrase") == text.index("target phrase")


def test_normalized_locator_uses_nearest_occurrence() -> None:
    text = "same evidence" + (" x" * 100) + " same evidence"
    locator = NormalizedLocator(text)
    assert locator.find("same evidence", near_ratio=0.95) == text.rindex("same evidence")


def test_diverse_selection_avoids_overlapping_neighbor() -> None:
    import numpy as np

    scores = np.array([1.0, 0.9, 0.8, 0.7, 0.6])
    selected = _select_diverse([0, 1, 2, 3, 4], scores, 3, 5)
    assert selected == [0, 2, 4]

