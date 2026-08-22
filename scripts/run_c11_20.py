"""Run C11: 10 BM25 + 8 BGE-M3 passages with strictly grounded graph hints."""

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
from c8_graph_passage import C8Context, LETTERS, answer_prompt, normalize_letter, question_type, retrieve_graph  # noqa: E402
from eval_four_datasets import OllamaClient  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, graph_path  # noqa: E402

VERSION = "c11-bm25-10-bgem3-8-grounded-graph-v1"
BASE = ROOT / "outputs" / "four_datasets"


def source_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]


def retrieval_rows() -> dict[tuple[str, int], dict[str, Any]]:
    payload = json.loads((BASE / "dqa_bgem3_retrieval_audit.json").read_text(encoding="utf-8"))
    if payload.get("model") != "bge-m3":
        raise RuntimeError("unexpected embedding audit model")
    return {(row["novel"], int(row["qi"])): row for row in payload["rows"]}


def select_ids(row: dict[str, Any]) -> list[int]:
    selected = []
    for raw in row["bm25_ids"][:10] + row["dense_ids"]:
        index = int(raw)
        if index in selected or any(abs(index - old) <= 1 for old in selected):
            continue
        selected.append(index)
        if len(selected) >= 18:
            break
    return selected


def package(ctx: C8Context, q: dict[str, Any], retrieval: dict[str, Any]) -> dict[str, Any]:
    selected = select_ids(retrieval)
    selected_chunk_ids = {ctx.base.chunks[index].id for index in selected}
    overlay = retrieve_graph(ctx, q, limit=18)
    links = []
    for link in overlay.get("links", []):
        evidence = str(link.get("evidence", "")).strip()
        evidence_norm = evidence.lower()
        source = str(link.get("source", "")).strip()
        target = str(link.get("target", "")).strip()
        if link.get("chunk_id") not in selected_chunk_ids or len(evidence) < 35:
            continue
        if source.lower() not in evidence_norm and target.lower() not in evidence_norm:
            continue
        links.append(link)
        if len(links) >= 5:
            break
    chunks = []
    bm25_set = set(map(int, retrieval["bm25_ids"][:10]))
    for index in sorted(selected):
        chunk = ctx.base.chunks[index]
        chunks.append({"id": chunk.id, "start": chunk.start, "end": chunk.end, "text": chunk.text, "score": 0.0, "source": "bm25" if index in bm25_set else "bge-m3"})
    return {"chunks": chunks, "links": links, "novel_length": len(ctx.base.novel_text), "diagnostics": {"selected": selected, "bm25_chunks": sum(index in bm25_set for index in selected), "dense_chunks": sum(index not in bm25_set for index in selected)}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen2.5:7b-32k")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--out", type=Path, default=BASE / "dqa_qwen_c11_20")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    retrieval = retrieval_rows()
    total = sum(len(cases[novel]["questions"]) for novel in args.novels)
    done = 0
    started = time.time()
    lock = threading.Lock()
    client = OllamaClient(args.model, max_tokens=600, num_ctx=32768)
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "version": VERSION, "source_hash": source_hash(), "model": args.model, "embedding_model": "bge-m3", "novels": args.novels, "budget": "18 passages: 10 BM25 then 8 dense"}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        jobs = []
        for qi, q in enumerate(case["questions"]):
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            valid = False
            if path.exists():
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                    valid = old.get("prompt_version") == VERSION and old.get("source_hash") == source_hash() and normalize_letter(old.get("selected_letter")) in LETTERS
                except Exception:
                    pass
            if valid:
                with lock:
                    done += 1
                continue
            jobs.append((qi, q, path))

        def one(job: tuple[int, dict[str, Any], Path]) -> dict[str, Any]:
            qi, q, path = job
            ctx = C8Context.build(graph, case["text"], None)
            evidence = package(ctx, q, retrieval[(novel, qi)])
            raw = client.complete_json("You are a careful small-context detective-novel reader. Use only supplied passages and return one answer.", answer_prompt(q, evidence), max_tokens=500)
            letter = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw)
            row = {"method": "c11_dense_graph", "selected_letter": letter, "selected_text": q["choices"][LETTERS.index(letter)] if letter in LETTERS else "", "reason": str(raw.get("reason", "")) if isinstance(raw, dict) else "", "retrieval": evidence, "raw": raw, "question_type": question_type(q["question"]), "prompt_version": VERSION, "source_hash": source_hash(), "novel": novel, "batch": "first10" if novel in FIRST10 else "second10", "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": LETTERS[q["gold_index"]], "correct": letter == LETTERS[q["gold_index"]], "answer_model": args.model}
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
            return row

        with ThreadPoolExecutor(max_workers=min(args.workers, len(jobs) or 1)) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for future in as_completed(futures):
                row = future.result()
                with lock:
                    done += 1
                    elapsed = max(time.time() - started, 0.01)
                    progress = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "completed": done, "total": total, "current": f"c11/{novel}/q{row['qi']} -> {row['selected_letter']}", "per_hour": done / elapsed * 3600, "eta_minutes": (total - done) / max(done / elapsed, 1e-9) / 60}
                    (args.out / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"[{done}/{total}] c11/{novel}/q{row['qi']} -> {row['selected_letter']}", flush=True)
    manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
