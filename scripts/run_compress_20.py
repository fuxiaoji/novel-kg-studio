"""Complete the historical whole-novel compression baseline on the trusted 20 novels."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from build_c_next10_graphs import merged_cases  # noqa: E402
from c_option_methods import LETTERS, normalize_letter  # noqa: E402
from detectiveqa_three_groups import (  # noqa: E402
    COMPRESS_CHUNK, COMPRESS_SUMMARY_CAP, answer_compressed,
)
from eval_four_datasets import OllamaClient  # noqa: E402
from novel_kg_studio.chunking import chunk_text  # noqa: E402
from run_c8_20 import FIRST10, NOVELS  # noqa: E402

VERSION = "historical-map-reduce-compression-v1"
MODEL = "qwen2.5:7b-32k"


def cache_dir(novel: str) -> Path:
    name = "dqa_qwen_c" if novel in FIRST10 else "dqa_qwen_c_next10"
    return ROOT / "outputs" / "four_datasets" / name / "novels" / novel


def answer_key(novel: str, qi: int, model: str) -> str:
    return hashlib.sha1(f"{novel}|{qi}|v1|{model}".encode("utf-8")).hexdigest()[:10]


def compression_letter(raw: str) -> str | None:
    letter = normalize_letter(raw)
    if letter:
        return letter
    text = str(raw or "")
    # Older cached generations occasionally used prose instead of the requested
    # one-letter form. Prefer their explicit final conclusion, then fall back to
    # common unambiguous answer phrasings. This is parser repair only: the cached
    # model output is never changed or regenerated.
    patterns = (
        r"(?i)most\s+fitting\s+answer\s+is\s*\(?([A-D])\)?",
        r"(?i)closest\s+match[\s\S]{0,100}?\b(?:is|be)\s*\(?([A-D])\)?",
        r"(?i)killer[\s\S]{0,80}?\bis\s*\(?([A-D])\)?",
        r"(?i)(?:incorrect\s+statement|option|choice)\s*(?:is\s*:|is|:)\s*\n?\s*([A-D])\s*[.\):]",
        r"(?i)\boption\s+([A-D])\b",
        r"(?m)^\s*([A-D])\s*[.)]\s+",
    )
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            return matches[-1].upper()
    return None


def compress_novel_parallel(client: Any, novel_text: str, out_dir: Path, workers: int) -> str:
    path = out_dir / "compressed.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["text"]

    def summarize(items: list[Any], level: str) -> list[str]:
        cache_dir = out_dir / "compress_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        results: list[str | None] = [None] * len(items)
        jobs = []
        for index, chunk in enumerate(items):
            cache = cache_dir / (f"chunk_{index}.json" if level == "chunk" else f"l2_{index}.json")
            if cache.exists():
                results[index] = json.loads(cache.read_text(encoding="utf-8"))["summary"]
            else:
                jobs.append((index, chunk, cache))

        def one(job: tuple[int, Any, Path]) -> tuple[int, str]:
            index, chunk, cache = job
            if level == "chunk":
                prompt = (
                    "Compress this novel excerpt into a concise fact summary. Keep: character names (verbatim), "
                    "places, times, actions, statements, clues, and any suspicious details. Preserve order. "
                    "Drop literary description and filler. Output only the summary.\n\nExcerpt:\n" + chunk.text
                )
                system = "You are an expert novel condensation editor."
                max_tokens = 2500
            else:
                prompt = (
                    "Compress this fact summary further into the most decision-relevant facts for answering "
                    "a detective question. Keep names and key clues. Output only the summary.\n\nSummary:\n" + chunk.text
                )
                system = "You are an expert novel condensation editor."
                max_tokens = 2000
            raw = ""
            for _ in range(3):
                try:
                    raw = client.complete(system, prompt, max_tokens=max_tokens)
                    if raw.strip():
                        break
                except Exception:
                    pass
            if not raw.strip():
                raise RuntimeError(f"compression failed {out_dir.name}/{level}/{index}")
            cache.write_text(json.dumps({"summary": raw}, ensure_ascii=False), encoding="utf-8")
            return index, raw

        with ThreadPoolExecutor(max_workers=min(workers, len(jobs) or 1)) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            completed = 0
            for future in as_completed(futures):
                index, raw = future.result()
                results[index] = raw
                completed += 1
                print(f"[summary] {out_dir.name}/{level} {completed}/{len(jobs)}", flush=True)
        return [str(value or "") for value in results]

    first = summarize(chunk_text(novel_text, size=COMPRESS_CHUNK, overlap=100), "chunk")
    combined = "\n".join(first)
    if len(combined) > COMPRESS_SUMMARY_CAP:
        combined = "\n".join(summarize(chunk_text(combined, size=COMPRESS_CHUNK, overlap=100), "l2"))
    path.write_text(json.dumps({"text": combined}, ensure_ascii=False), encoding="utf-8")
    return combined


class Progress:
    def __init__(self, root: Path, total: int) -> None:
        self.root = root
        self.total = total
        self.done = 0
        self.started = time.time()
        self.lock = threading.Lock()

    def record(self, current: str) -> None:
        with self.lock:
            self.done += 1
            elapsed = max(time.time() - self.started, 0.01)
            payload = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "completed": self.done,
                "total": self.total,
                "current": current,
                "per_hour": self.done / elapsed * 3600,
                "eta_minutes": (self.total - self.done) / max(self.done / elapsed, 1e-9) / 60,
            }
            (self.root / "progress.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--summary-workers", type=int, default=3)
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_compress20")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    progress = Progress(args.out, sum(len(cases[novel]["questions"]) for novel in args.novels))
    client = OllamaClient(args.model, max_tokens=2500, num_ctx=32768)
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "model": args.model,
        "novels": args.novels,
        "definition": "map 6000-character overlapping chunks to fact summaries; optional second compression above 40000 characters",
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for novel in args.novels:
        case = cases[novel]
        work = cache_dir(novel)
        work.mkdir(parents=True, exist_ok=True)
        compressed = compress_novel_parallel(client, case["text"], work, args.summary_workers)
        if not compressed.strip():
            raise RuntimeError(f"compression failed: {novel}")

        jobs = []
        for qi, q in enumerate(case["questions"]):
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            if path.exists():
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                    if row.get("version") == VERSION and normalize_letter(row.get("selected_letter")) in LETTERS:
                        progress.record(f"cached {novel}/q{qi}")
                        continue
                except Exception:
                    pass
            jobs.append((qi, q, path))

        def one(job: tuple[int, dict[str, Any], Path]) -> dict[str, Any]:
            qi, q, path = job
            key = answer_key(novel, qi, args.model)
            raw = answer_compressed(client, q, compressed, work, key)
            letter = compression_letter(raw)
            row = {
                "version": VERSION,
                "novel": novel,
                "qi": qi,
                "qid": q["qid"],
                "gold_letter": LETTERS[q["gold_index"]],
                "selected_letter": letter,
                "correct": letter == LETTERS[q["gold_index"]],
                "answer": raw,
                "summary_chars": len(compressed),
                "model": args.model,
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
            return row

        with ThreadPoolExecutor(max_workers=min(args.workers, len(jobs) or 1)) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for future in as_completed(futures):
                row = future.result()
                progress.record(f"compress/{novel}/q{row['qi']} -> {row['selected_letter']}")
                print(f"[{progress.done}/{progress.total}] compress/{novel}/q{row['qi']} -> {row['selected_letter']}", flush=True)
    manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
