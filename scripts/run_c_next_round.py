"""Run the next option-C experiment round on existing graphs with live progress."""

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

from analyze_c_improvements import old_answer
from c_next_methods import VERSION, normalize_letter, run_evidence_verifier, run_next_method
from eval_four_datasets import OllamaClient
from run_c_improvements import NOVELS, ProgressReporter, graph_manifest, merged_cases

METHODS = ["c4fix", "c1perm", "c3disc", "gate"]


def source_hash() -> str:
    return hashlib.sha1((ROOT / "scripts" / "c_next_methods.py").read_bytes()).hexdigest()[:12]


def answer_path(root: Path, method: str, mask: str, novel: str, qi: int) -> Path:
    return root / method / mask / novel / f"q{qi:02d}.json"


def valid_cached(path: Path) -> bool:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return row.get("prompt_version") == VERSION and row.get("prompt_hash") == source_hash() and normalize_letter(row.get("selected_letter")) is not None
    except Exception:
        return False


def enrich(result: dict[str, Any], q: dict[str, Any], novel: str, qi: int, mask: str, model: str) -> dict[str, Any]:
    result.update(
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
        }
    )
    return result


def run_base_method(args: argparse.Namespace, method: str, mask: str, cases: dict[str, dict], before: dict[str, dict], reporter: ProgressReporter) -> None:
    client = OllamaClient(args.answer_model, max_tokens=3000, num_ctx=32768)
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(Path(before[novel]["path"]).read_text(encoding="utf-8"))
        jobs = []
        for qi, q in enumerate(case["questions"][: args.max_questions or None]):
            path = answer_path(args.out_root, method, mask, novel, qi)
            if valid_cached(path):
                reporter.seed_cached(method, mask, json.loads(path.read_text(encoding="utf-8")))
            else:
                jobs.append((qi, q, path))
        if not jobs:
            print(f"[{method}/{mask}/{novel}] cached", flush=True)
            continue
        started = time.time()

        def one(job: tuple[int, dict[str, Any], Path]) -> tuple[int, dict[str, Any]]:
            qi, q, path = job
            mask_char = q.get("mask_char") if mask == "masked" else None
            result = run_next_method(method, client, q, graph, case["text"], mask_char)
            enrich(result, q, novel, qi, mask, args.answer_model)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            return qi, result

        with ThreadPoolExecutor(max_workers=min(args.answer_workers, len(jobs))) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for done, future in enumerate(as_completed(futures), 1):
                qi, result = future.result()
                print(f"[{method}/{mask}/{novel}] {done}/{len(jobs)} q{qi} -> {result.get('selected_letter')} ({time.time()-started:.0f}s)", flush=True)
                reporter.record(method, mask, result, f"{method} / {mask} / 小说 {novel} / q{qi}")


def run_gate(args: argparse.Namespace, mask: str, cases: dict[str, dict], reporter: ProgressReporter) -> None:
    client = OllamaClient(args.answer_model, max_tokens=3000, num_ctx=32768)
    for novel in args.novels:
        case = cases[novel]
        jobs = []
        for qi, q in enumerate(case["questions"][: args.max_questions or None]):
            path = answer_path(args.out_root, "gate", mask, novel, qi)
            if valid_cached(path):
                reporter.seed_cached("gate", mask, json.loads(path.read_text(encoding="utf-8")))
            else:
                jobs.append((qi, q, path))
        if not jobs:
            print(f"[gate/{mask}/{novel}] cached", flush=True)
            continue
        started = time.time()

        def one(job: tuple[int, dict[str, Any], Path]) -> tuple[int, dict[str, Any]]:
            qi, q, path = job
            candidates = []
            for method in ("c4fix", "c1perm", "c3disc"):
                source = answer_path(args.out_root, method, mask, novel, qi)
                if not source.exists():
                    raise FileNotFoundError(f"gate requires {source}")
                candidates.append(json.loads(source.read_text(encoding="utf-8")))
            tail, _ = old_answer(args.graph_root, novel, qi, "tail", mask)
            result = run_evidence_verifier(client, q, candidates, tail)
            enrich(result, q, novel, qi, mask, args.answer_model)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            return qi, result

        with ThreadPoolExecutor(max_workers=min(args.answer_workers, len(jobs))) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for done, future in enumerate(as_completed(futures), 1):
                qi, result = future.result()
                print(f"[gate/{mask}/{novel}] {done}/{len(jobs)} q{qi} -> {result.get('selected_letter')} ({time.time()-started:.0f}s)", flush=True)
                reporter.record("gate", mask, result, f"gate / {mask} / 小说 {novel} / q{qi}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c")
    parser.add_argument("--out-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_next_round")
    parser.add_argument("--methods", nargs="+", choices=METHODS, default=METHODS)
    parser.add_argument("--masks", nargs="+", choices=["masked", "unmasked"], default=["masked", "unmasked"])
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--answer-model", default="qwen2.5:7b-32k")
    parser.add_argument("--answer-workers", type=int, default=3)
    parser.add_argument("--max-questions", type=int, default=0)
    args = parser.parse_args()
    args.out_root.mkdir(parents=True, exist_ok=True)
    cases = merged_cases()
    before = graph_manifest(args.graph_root, args.novels)
    ordered = [m for m in METHODS if m in args.methods]
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "graph_root": str(args.graph_root),
        "out_root": str(args.out_root),
        "reuse_graph_only": True,
        "answer_model": args.answer_model,
        "methods": ordered,
        "masks": args.masks,
        "prompt_version": VERSION,
        "prompt_hash": source_hash(),
        "graphs_before": before,
    }
    (args.out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    reporter = ProgressReporter(args.out_root, cases, ordered, args.novels, args.max_questions, args.masks)
    for method in ordered:
        for mask in args.masks:
            if method == "gate":
                run_gate(args, mask, cases, reporter)
            else:
                run_base_method(args, method, mask, cases, before, reporter)
    after = graph_manifest(args.graph_root, args.novels)
    if before != after:
        raise RuntimeError("Read-only graph invariant violated")
    manifest["graphs_after"] = after
    manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (args.out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
