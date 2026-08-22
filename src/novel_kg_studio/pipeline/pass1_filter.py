from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from ..cache import load_json, save_json
from ..chunking import TextChunk, chunk_text, find_span, sentence_spans
from ..schema import DroppedSpan, KeptSpan, norm_text, parse_time_label, period_rank

PASS1_SYSTEM = """You are an expert novel condensation editor.
Given a chunk of a detective novel, split it into:
- kept: verbatim continuous spans that carry plot information (actions, clues, character movements, times, locations, statements, events).
- dropped: verbatim spans that are purely literary (description, filler dialogue, scene setting, digression).

Rules:
- Every text value MUST be a verbatim continuous span copied from the chunk. Do not paraphrase.
- Keep spans short (<= 300 chars); split long passages into multiple spans.
- Still drop pure literary padding even inside plot-heavy passages.
- For each kept span give a time_label like "Day 1 morning", "Day 2 night", or "unknown".

Return strict JSON only:
{"kept":[{"text":"...","time_label":"..."}],"dropped":[{"text":"...","reason":"literary_description|filler_dialogue|scene_setting|digression|other"}]}"""


def build_pass1_user(chunk: TextChunk) -> str:
    return f"Chunk:\n{chunk.text}"


def parse_pass1_payload(payload: Any, chunk: TextChunk) -> tuple[list[KeptSpan], list[DroppedSpan], int]:
    kept: list[KeptSpan] = []
    dropped: list[DroppedSpan] = []
    skipped = 0
    if not isinstance(payload, dict):
        return kept, dropped, 1
    for idx, item in enumerate(payload.get("kept") or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start, end = find_span(chunk.text, text)
        if start < 0:
            skipped += 1
            continue
        day, period = parse_time_label(item.get("time_label"))
        kept.append(
            KeptSpan(
                text=text,
                chunk_idx=int(str(chunk.id).split("_")[-1]),
                span_idx=idx,
                char_start=chunk.start + start,
                char_end=chunk.start + end,
                time_label=str(item.get("time_label") or "unknown"),
                day=day,
                period=period,
            )
        )
    for idx, item in enumerate(payload.get("dropped") or []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start, end = find_span(chunk.text, text)
        if start < 0:
            skipped += 1
            continue
        reason = str(item.get("reason") or "other")
        dropped.append(
            DroppedSpan(
                text=text,
                reason=reason,
                chunk_idx=int(str(chunk.id).split("_")[-1]),
                char_start=chunk.start + start,
                char_end=chunk.start + end,
            )
        )
    return kept, dropped, skipped


def _process_chunk(
    chunk: TextChunk,
    client: Any,
    cache_dir: Path,
    resume: bool,
    max_tokens: int,
) -> dict[str, Any]:
    path = cache_dir / f"{chunk.id}.json"
    if resume:
        cached = load_json(path)
        if cached is not None and not cached.get("error"):
            return cached
    try:
        payload = client.complete_json(PASS1_SYSTEM, build_pass1_user(chunk), max_tokens=max_tokens)
        kept, dropped, skipped = parse_pass1_payload(payload, chunk)
        error = ""
    except Exception as exc:
        kept = [
            KeptSpan(
                text=text,
                chunk_idx=int(str(chunk.id).split("_")[-1]),
                span_idx=idx,
                char_start=chunk.start + start,
                char_end=chunk.start + end,
                time_label="unknown",
            )
            for idx, (start, end, text) in enumerate(sentence_spans(chunk.text))
        ]
        dropped, skipped = [], 0
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    record = {
        "chunk_id": chunk.id,
        "chunk_start": chunk.start,
        "chunk_end": chunk.end,
        "kept": [k.to_dict() for k in kept],
        "dropped": [d.to_dict() for d in dropped],
        "skipped": skipped,
        "error": error,
    }
    save_json(path, record)
    return record


def run_pass1(
    novel_text: str,
    *,
    config: dict[str, Any],
    client: Any,
    out_dir: Path,
    resume: bool = True,
    workers: int = 8,
    max_chunks: int | None = None,
    log: Callable[[str], None] = print,
) -> tuple[list[KeptSpan], list[DroppedSpan], dict[str, Any]]:
    chunk_cfg = config.get("chunking") or {}
    size = int(chunk_cfg.get("size") or 1500)
    overlap = int(chunk_cfg.get("overlap") or 100)
    chunks = chunk_text(novel_text, size=size, overlap=overlap)
    if max_chunks is not None:
        chunks = chunks[:max_chunks]
    cache_dir = out_dir / "pass1" / f"s{size}_o{overlap}"
    max_tokens = int((config.get("model") or {}).get("max_tokens_pass1") or 2500)

    log(f"[pass1] chunks={len(chunks)} workers={workers} resume={resume}")
    records: list[dict[str, Any]] = [None] * len(chunks)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
        futures = {
            pool.submit(_process_chunk, chunk, client, cache_dir, resume, max_tokens): idx
            for idx, chunk in enumerate(chunks)
        }
        done = 0
        for future in as_completed(futures):
            idx = futures[future]
            records[idx] = future.result()
            done += 1
            if done % 20 == 0 or done == len(chunks):
                log(f"[pass1] {done}/{len(chunks)} chunks")

    kept_all: list[KeptSpan] = []
    seen: set[str] = set()
    dropped_all: list[DroppedSpan] = []
    failed = 0
    for record in records:
        if not record:
            continue
        if record.get("error"):
            failed += 1
        for row in record.get("kept") or []:
            key = norm_text(row["text"])
            if key in seen:
                continue
            seen.add(key)
            kept_all.append(
                KeptSpan(
                    text=row["text"],
                    chunk_idx=row["chunk_idx"],
                    span_idx=row["span_idx"],
                    char_start=row["char_start"],
                    char_end=row["char_end"],
                    time_label=row.get("time_label") or "unknown",
                    day=row.get("day"),
                    period=row.get("period") or "unknown",
                )
            )
        for row in record.get("dropped") or []:
            dropped_all.append(
                DroppedSpan(
                    text=row["text"],
                    reason=row.get("reason") or "other",
                    chunk_idx=row["chunk_idx"],
                    char_start=row["char_start"],
                    char_end=row["char_end"],
                )
            )

    kept_all.sort(key=lambda s: (s.chunk_idx, s.span_idx))
    # forward/backward day fill
    days = [s.day for s in kept_all]
    filled = list(days)
    last = None
    for i, d in enumerate(filled):
        if d is not None:
            last = d
        elif last is not None:
            filled[i] = last
    last = None
    for i in range(len(filled) - 1, -1, -1):
        if filled[i] is not None:
            last = filled[i]
        elif last is not None:
            filled[i] = last
    has_days = any(d is not None for d in filled)
    for i, s in enumerate(kept_all):
        s.day_filled = float(filled[i]) if has_days else float(s.chunk_idx)
    if has_days:
        day_min = min(float(d) for d in filled if d is not None)
        day_max = max(float(d) for d in filled if d is not None)
        for s in kept_all:
            s.time_position = (s.day_filled - day_min) / (day_max - day_min) if day_max > day_min else 0.5
    else:
        chunk_count = max(len(chunks), 1)
        for s in kept_all:
            s.time_position = min(s.day_filled / chunk_count, 1.0)
    for s in kept_all:
        s.text_position = s.char_start / max(len(novel_text), 1)

    kept_all.sort(key=lambda s: (s.day_filled, period_rank(s.period), s.chunk_idx, s.span_idx))
    for seq, s in enumerate(kept_all):
        s.seq = seq

    novel_len = max(len(novel_text), 1)
    for d in dropped_all:
        d.text_position = d.char_start / novel_len

    dropped_by_reason: dict[str, int] = {}
    for d in dropped_all:
        dropped_by_reason[d.reason] = dropped_by_reason.get(d.reason, 0) + len(d.text)
    stats = {
        "novel_chars": len(novel_text),
        "num_chunks": len(chunks),
        "failed_chunks": failed,
        "num_kept": len(kept_all),
        "kept_chars": sum(len(s.text) for s in kept_all),
        "num_dropped": len(dropped_all),
        "dropped_chars": sum(len(d.text) for d in dropped_all),
        "dropped_by_reason": dropped_by_reason,
        "time_labels_seen": sorted({s.time_label for s in kept_all if s.day is not None}),
    }
    stats["kept_ratio"] = stats["kept_chars"] / novel_len
    save_json(cache_dir / "stats.json", stats)
    return kept_all, dropped_all, stats
