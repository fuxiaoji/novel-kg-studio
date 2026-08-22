from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

NODE_TYPES = ("person", "location", "time_anchor", "clue_object", "event", "evidence_sentence")
EDGE_TYPES = (
    "located_at",
    "appears_at",
    "belongs_to",
    "mentions",
    "temporal_sequence",
    "supports",
    "contradicts",
    "related_to",
    "motive",
    "means",
    "opportunity",
    "witnessed_by",
)
DROP_REASONS = ("literary_description", "filler_dialogue", "scene_setting", "digression", "other")

PERIOD_RANK = {"unknown": 0, "morning": 1, "noon": 2, "afternoon": 3, "evening": 4, "night": 5}

TYPE_LAYER = {name: idx for idx, name in enumerate(NODE_TYPES)}
TYPE_COLORS = {
    "person": "#4e79a7",
    "location": "#59a14f",
    "time_anchor": "#b6992d",
    "clue_object": "#f28e2b",
    "event": "#e15759",
    "evidence_sentence": "#b07aa1",
}


@dataclass
class KeptSpan:
    text: str
    chunk_idx: int
    span_idx: int
    char_start: int = -1
    char_end: int = -1
    time_label: str = "unknown"
    day: int | None = None
    period: str = "unknown"
    seq: int = 0
    day_filled: float = 0.0
    time_position: float = 0.5
    text_position: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "chunk_idx": self.chunk_idx,
            "span_idx": self.span_idx,
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "time_label": self.time_label,
            "day": self.day,
            "period": self.period,
            "day_filled": self.day_filled,
            "time_position": self.time_position,
            "text_position": self.text_position,
        }


@dataclass
class DroppedSpan:
    text: str
    reason: str
    chunk_idx: int
    char_start: int = -1
    char_end: int = -1
    text_position: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_idx": self.chunk_idx,
            "text": self.text,
            "reason": self.reason,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "text_position": self.text_position,
        }


def parse_time_label(label: str) -> tuple[int | None, str]:
    """Parse 'Day 2, morning' / 'D3 night' / 'unknown' into (day, period)."""
    text = str(label or "").strip().lower()
    day = None
    match = re.search(r"(?:day|d)\s*(\d+)", text)
    if match:
        day = int(match.group(1))
    period = "unknown"
    for cand in ("morning", "noon", "afternoon", "evening", "night"):
        if cand in text:
            period = cand
            break
    return day, period


def period_rank(period: str) -> int:
    return PERIOD_RANK.get(str(period or "").lower(), 0)


def norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def canonical_name(value: str) -> str:
    text = norm_text(value)
    text = re.sub(r"^the\s+", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text).strip()
    return text

