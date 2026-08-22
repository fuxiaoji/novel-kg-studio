from __future__ import annotations

import re
from dataclasses import dataclass

from .schema import norm_text


@dataclass
class TextChunk:
    id: str
    start: int
    end: int
    text: str


def chunk_text(text: str, size: int = 1500, overlap: int = 100, prefix: str = "chunk") -> list[TextChunk]:
    step = max(size - overlap, 1)
    chunks: list[TextChunk] = []
    pos = 0
    idx = 0
    while pos < len(text):
        end = min(pos + size, len(text))
        chunks.append(TextChunk(f"{prefix}_{idx}", pos, end, text[pos:end]))
        if end == len(text):
            break
        pos += step
        idx += 1
    return chunks


def chunk_lines(lines: list[str], size: int = 1500, prefix: str = "pass2") -> list[tuple[TextChunk, list[int]]]:
    """Pack whole lines into chunks (no mid-line cuts); returns (chunk, line_indices)."""
    chunks: list[tuple[TextChunk, list[int]]] = []
    current: list[int] = []
    current_len = 0
    pos = 0
    idx = 0
    for line_idx, line in enumerate(lines):
        if current and current_len + len(line) + 1 > size:
            text = "\n".join(lines[i] for i in current)
            chunks.append((TextChunk(f"{prefix}_{idx}", pos, pos + len(text), text), list(current)))
            pos += len(text) + 1
            idx += 1
            current = []
            current_len = 0
        current.append(line_idx)
        current_len += len(line) + 1
    if current:
        text = "\n".join(lines[i] for i in current)
        chunks.append((TextChunk(f"{prefix}_{idx}", pos, pos + len(text), text), list(current)))
    return chunks


_SENT_RE = re.compile(r"[^.!?\u3002\uff01\uff1f]+[.!?\u3002\uff01\uff1f]*")


def sentence_spans(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0).strip()) for m in _SENT_RE.finditer(text) if m.group(0).strip()]


def find_span(text: str, span: str) -> tuple[int, int]:
    """Locate a (normalized) span inside text; returns char offsets or (-1,-1)."""
    normalized = norm_text(text)
    target = norm_text(span)
    if not target:
        return (-1, -1)
    idx = normalized.find(target)
    if idx < 0:
        return (-1, -1)
    return (idx, idx + len(target))

