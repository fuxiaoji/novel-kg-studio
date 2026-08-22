"""Run the official-clue oracle baseline on the trusted 20 DetectiveQA novels."""

from __future__ import annotations

import argparse
import hashlib
import json
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
from eval_four_datasets import OllamaClient  # noqa: E402
from goldonly_baseline import load_anno, paragraph_map  # noqa: E402
from run_c8_20 import FIRST10, NOVELS  # noqa: E402

MODEL = "qwen2.5:7b-32k"
VERSION = "official-clue-position-unmasked-v1"


def legacy_cache(novel: str, source: str, qi: int, model: str) -> Path:
    key = hashlib.sha1(f"{novel}|{source}|{qi}|unmasked|v1|{model}".encode("utf-8")).hexdigest()[:10]
    return ROOT / "outputs" / "four_datasets" / "dqa_qwen_c" / "goldonly" / novel / f"unmasked_{key}.json"


def load_legacy_answer(novel: str, source: str, qi: int, model: str) -> str | None:
    if novel not in FIRST10:
        return None
    path = legacy_cache(novel, source, qi, model)
    if not path.exists():
        return None
    return str(json.loads(path.read_text(encoding="utf-8")).get("answer") or "") or None


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
            data = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "completed": self.done,
                "total": self.total,
                "current": current,
                "per_hour": self.done / elapsed * 3600,
                "eta_minutes": (self.total - self.done) / max(self.done / elapsed, 1e-9) / 60,
            }
            (self.root / "progress.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_goldonly20")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    progress = Progress(args.out, sum(len(cases[n]["questions"]) for n in args.novels))
    client = OllamaClient(args.model, max_tokens=600, num_ctx=32768)
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "model": args.model,
        "novels": args.novels,
        "definition": "question + options + every nonnegative official clue_position paragraph; no full novel, answer paragraph, or official reasoning",
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    for novel in args.novels:
        annotations = {(r["source"], int(r["qi"])): r for r in load_anno(novel)}
        paragraphs = paragraph_map(novel)
        jobs: list[tuple[int, dict[str, Any], dict[str, Any], Path]] = []
        for qi, question in enumerate(cases[novel]["questions"]):
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            if path.exists():
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                    if row.get("version") == VERSION and normalize_letter(row.get("selected_letter")) in LETTERS:
                        progress.record(f"cached {novel}/q{qi}")
                        continue
                except Exception:
                    pass
            tail = question["qid"].removeprefix(f"detectiveqa_{novel}_")
            source, source_qi = tail.rsplit("_", 1)
            annotation = annotations[(source, int(source_qi))]
            jobs.append((qi, question, annotation, path))

        def one(job: tuple[int, dict[str, Any], dict[str, Any], Path]) -> dict[str, Any]:
            qi, question, annotation, path = job
            raw = load_legacy_answer(novel, annotation["source"], int(annotation["qi"]), args.model)
            clue_positions = [int(p) for p in annotation["clue_position"] if isinstance(p, (int, float)) and p >= 0 and int(p) in paragraphs]
            evidence = "\n\n".join(paragraphs[p] for p in clue_positions)
            options = "Options:\n" + "\n".join(f"{letter}. {text}" for letter, text in sorted(annotation["options"].items()))
            prompt = (
                f"Question: {annotation['question']}\n\n{options}\n\nEvidence:\n{evidence}\n\n"
                "Answer with the option letter and its text."
            )
            if raw is None:
                raw = client.complete(
                    "You are a careful detective-novel reader using ONLY the provided evidence.",
                    prompt,
                    max_tokens=600,
                )
            letter = normalize_letter(raw)
            gold = LETTERS[question["gold_index"]]
            row = {
                "version": VERSION,
                "novel": novel,
                "batch": "first10" if novel in FIRST10 else "second10",
                "qi": qi,
                "qid": question["qid"],
                "question": question["question"],
                "choices": question["choices"],
                "gold_letter": gold,
                "selected_letter": letter,
                "correct": letter == gold,
                "answer": raw,
                "clue_positions": clue_positions,
                "evidence_chars": len(evidence),
                "model": args.model,
                "reused_legacy_first10": novel in FIRST10,
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
            return row

        with ThreadPoolExecutor(max_workers=min(args.workers, len(jobs) or 1)) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for future in as_completed(futures):
                row = future.result()
                progress.record(f"{novel}/q{row['qi']} -> {row['selected_letter']}")
                print(f"[{progress.done}/{progress.total}] gold/{novel}/q{row['qi']} -> {row['selected_letter']}", flush=True)

    rows = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((args.out / "answers").glob("*/*.json"))]
    sets = {
        "all": rows,
        "first10": [r for r in rows if r["batch"] == "first10"],
        "second10": [r for r in rows if r["batch"] == "second10"],
    }
    summary = {
        name: {
            "correct": sum(bool(r["correct"]) for r in subset),
            "total": len(subset),
            "parsed": sum(bool(normalize_letter(r.get("selected_letter"))) for r in subset),
            "accuracy": sum(bool(r["correct"]) for r in subset) / len(subset) if subset else 0.0,
        }
        for name, subset in sets.items()
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
