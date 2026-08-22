"""Run the five selected methods plus tail baseline on the second ten novels."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from build_c_next10_graphs import NOVELS, merged_cases
from c_next_methods import run_next_method
from c_option_methods import LETTERS, normalize_letter, run_method
from detectiveqa_three_groups import answer_tail
from eval_four_datasets import OllamaClient
from run_c_improvements import ProgressReporter, graph_manifest

BASE_METHODS = ["tail", "c1", "c2", "c3support", "c4", "c3first"]
REPORT_METHODS = ["c1", "c2", "c4", "c3first", "c6"]
ALL_METHODS = [*BASE_METHODS, "c6"]


def file_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


HASHES = {
    "tail": file_hash(Path(__file__)),
    "c1": file_hash(ROOT / "scripts" / "c_option_methods.py"),
    "c2": file_hash(ROOT / "scripts" / "c_option_methods.py"),
    "c3support": file_hash(ROOT / "scripts" / "c_option_methods.py"),
    "c4": file_hash(ROOT / "scripts" / "c_option_methods.py"),
    "c3first": file_hash(ROOT / "scripts" / "c_next_methods.py"),
    "c6": file_hash(Path(__file__)),
}


def out_path(root: Path, method: str, mask: str, novel: str, qi: int) -> Path:
    return root / method / mask / novel / f"q{qi:02d}.json"


def valid_cached(path: Path, method: str) -> bool:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return row.get("experiment_hash") == HASHES[method] and normalize_letter(row.get("selected_letter")) is not None
    except Exception:
        return False


def enrich(row: dict[str, Any], q: dict[str, Any], novel: str, qi: int, mask: str, model: str, method: str) -> dict[str, Any]:
    row.update(
        {
            "novel": novel,
            "qi": qi,
            "qid": q["qid"],
            "question": q["question"],
            "choices": q["choices"],
            "gold_index": q.get("gold_index"),
            "gold_text": q.get("gold_text"),
            "mask": mask,
            "answer_model": model,
            "experiment_hash": HASHES[method],
        }
    )
    return row


def run_base(args: argparse.Namespace, method: str, mask: str, cases: dict[str, dict], before: dict[str, dict], reporter: ProgressReporter) -> None:
    client = OllamaClient(args.answer_model, max_tokens=3000, num_ctx=32768)
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(Path(before[novel]["path"]).read_text(encoding="utf-8")) if method != "tail" else {}
        jobs = []
        for qi, q in enumerate(case["questions"]):
            path = out_path(args.out_root, method, mask, novel, qi)
            if valid_cached(path, method):
                reporter.seed_cached(method, mask, json.loads(path.read_text(encoding="utf-8")))
            else:
                jobs.append((qi, q, path))
        if not jobs:
            print(f"[{method}/{mask}/{novel}] cached", flush=True)
            continue
        started = time.time()

        def one(job: tuple[int, dict[str, Any], Path]) -> tuple[int, dict[str, Any]]:
            qi, q, path = job
            if method == "tail":
                raw_dir = args.out_root / "tail_raw" / novel
                raw_dir.mkdir(parents=True, exist_ok=True)
                key = hashlib.sha1(f"{novel}|{qi}|v1|{args.answer_model}".encode("utf-8")).hexdigest()[:10]
                raw = answer_tail(client, q, case["text"], raw_dir, key)
                letter = normalize_letter(raw)
                result = {
                    "selected_letter": letter,
                    "selected_text": q["choices"][LETTERS.index(letter)] if letter is not None and letter in LETTERS else "",
                    "confidence": "low",
                    "evidence_ids": [],
                    "reason": str(raw),
                    "method": "tail_window_baseline",
                    "raw": raw,
                }
            else:
                mask_char = q.get("mask_char") if mask == "masked" else None
                if method == "c3first":
                    result = run_next_method("c3first", client, q, graph, case["text"], mask_char)
                else:
                    mapped = "c3" if method == "c3support" else method
                    result = run_method(mapped, client, q, graph, case["text"], mask_char)
            enrich(result, q, novel, qi, mask, args.answer_model, method)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            return qi, result

        with ThreadPoolExecutor(max_workers=min(args.answer_workers, len(jobs))) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for done, future in enumerate(as_completed(futures), 1):
                qi, result = future.result()
                print(f"[{method}/{mask}/{novel}] {done}/{len(jobs)} q{qi} -> {result.get('selected_letter')} ({time.time()-started:.0f}s)", flush=True)
                reporter.record(method, mask, result, f"{method} / {mask} / 小说 {novel} / q{qi}")


def compact(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("method", "selected_letter", "confidence", "evidence_ids", "reason", "evidence")}


def run_c6(args: argparse.Namespace, mask: str, cases: dict[str, dict], reporter: ProgressReporter) -> None:
    client = OllamaClient(args.answer_model, max_tokens=2200, num_ctx=32768)
    for novel in args.novels:
        jobs = []
        for qi, q in enumerate(cases[novel]["questions"]):
            path = out_path(args.out_root, "c6", mask, novel, qi)
            if valid_cached(path, "c6"):
                reporter.seed_cached("c6", mask, json.loads(path.read_text(encoding="utf-8")))
            else:
                jobs.append((qi, q, path))
        started = time.time()

        def one(job: tuple[int, dict[str, Any], Path]) -> tuple[int, dict[str, Any]]:
            qi, q, path = job
            candidates = [compact(json.loads(out_path(args.out_root, method, mask, novel, qi).read_text(encoding="utf-8"))) for method in ("c1", "c2", "c3support", "c4")]
            letters = [normalize_letter(r.get("selected_letter")) for r in candidates]
            agreement = len(set(letters)) == 1 and letters[0] is not None and all(r.get("confidence") in {"high", "medium"} and r.get("evidence_ids") for r in candidates)
            raw: dict[str, Any] = {}
            if agreement:
                winner = letters[0]
                result = {"selected_letter": winner, "selected_text": q["choices"][LETTERS.index(winner)], "confidence": "high", "evidence_ids": sorted({x for r in candidates for x in (r.get("evidence_ids") or [])}), "reason": "All four grounded candidates agree.", "arbitrated": False}
            else:
                prompt = (
                    f"Question: {q['question']}\nOptions:\n"
                    + "\n".join(f"{LETTERS[i]}. {v}" for i, v in enumerate(q["choices"][:4]))
                    + "\n\nIndependent candidates and evidence summaries:\n"
                    + json.dumps(candidates, ensure_ascii=False)
                    + '\n\nDo not vote by count. Prefer explicit relevant evidence; absence is not contradiction. Return strict JSON: {"selected_letter":"A|B|C|D","confidence":"high|medium|low","reason":"brief","evidence_ids":[]}'
                )
                raw = client.complete_json("Arbitrate only from supplied evidence summaries; never use a gold answer.", prompt, max_tokens=1800)
                winner = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else None)
                if winner is None:
                    ranked = sorted(candidates, key=lambda r: ({"high": 2, "medium": 1, "low": 0}.get(str(r.get("confidence")), 0), len(r.get("evidence_ids") or [])), reverse=True)
                    winner = normalize_letter(ranked[0].get("selected_letter")) or "A"
                result = {"selected_letter": winner, "selected_text": q["choices"][LETTERS.index(winner)], "confidence": str(raw.get("confidence", "low")) if isinstance(raw, dict) else "low", "evidence_ids": raw.get("evidence_ids", []) if isinstance(raw, dict) else [], "reason": str(raw.get("reason", "")) if isinstance(raw, dict) else "", "arbitrated": True, "raw": raw}
            result.update({"method": "c6_evidence_arbiter", "candidates": candidates})
            enrich(result, q, novel, qi, mask, args.answer_model, "c6")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            return qi, result

        with ThreadPoolExecutor(max_workers=min(args.answer_workers, len(jobs) or 1)) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for done, future in enumerate(as_completed(futures), 1):
                qi, result = future.result()
                print(f"[c6/{mask}/{novel}] {done}/{len(jobs)} q{qi} -> {result.get('selected_letter')} ({time.time()-started:.0f}s)", flush=True)
                reporter.record("c6", mask, result, f"c6 / {mask} / 小说 {novel} / q{qi}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_next10")
    parser.add_argument("--out-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_next10_methods")
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--methods", nargs="+", choices=ALL_METHODS, default=ALL_METHODS)
    parser.add_argument("--masks", nargs="+", choices=["masked", "unmasked"], default=["masked", "unmasked"])
    parser.add_argument("--answer-model", default="qwen2.5:7b-32k")
    parser.add_argument("--answer-workers", type=int, default=3)
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    before = graph_manifest(args.graph_root, args.novels)
    args.out_root.mkdir(parents=True, exist_ok=True)
    ordered = [method for method in ALL_METHODS if method in args.methods]
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "novels": args.novels, "reported_methods": REPORT_METHODS, "internal_support_method": "c3support", "baseline": "tail", "answer_model": args.answer_model, "methods": ordered, "masks": args.masks, "graphs_before": before}
    (args.out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    reporter = ProgressReporter(args.out_root, cases, ordered, args.novels, 0, args.masks)
    for method in ordered:
        for mask in args.masks:
            if method == "c6":
                run_c6(args, mask, cases, reporter)
            else:
                run_base(args, method, mask, cases, before, reporter)
    after = graph_manifest(args.graph_root, args.novels)
    if before != after:
        raise RuntimeError("graph hash or timestamp changed during answer-only run")
    manifest["graphs_after"] = after
    manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (args.out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
