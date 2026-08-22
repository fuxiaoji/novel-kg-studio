"""DetectiveQA full-novel baseline: DeepSeek v4-flash reads the ENTIRE novel per question.

Pure completion call (no retrieval, no browsing). This is a leaky upper bound: the
answer paragraphs are inside the context.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_four_datasets import UrllibClient, load_cases, options_block  # noqa: E402

OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"
PROMPT_VER = "v1"


def merge_cases() -> list[dict]:
    by_novel: dict[str, dict] = {}
    for c in load_cases("detectiveqa"):
        nid = str(c["meta"]["novel_id"])
        merged = by_novel.setdefault(
            nid,
            {"dataset": "detectiveqa", "case_id": f"detectiveqa_{nid}", "title": c["title"], "text": c["text"], "meta": {"novel_id": nid}, "questions": []},
        )
        seen = {q["question"] for q in merged["questions"]}
        for q in c["questions"]:
            if q["question"] not in seen:
                seen.add(q["question"])
                merged["questions"].append(q)
    return [by_novel[nid] for nid in sorted(by_novel, key=int)]


def answer_one(client, case: dict, qi: int, q: dict, out_dir: Path, key: str) -> str:
    path = out_dir / f"fullnovel_{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["answer"]
    opt = options_block(q)
    prompt = (
        f"Question: {q['question']}\n\n{opt}\n\nNovel (complete text):\n{case['text']}\n\n"
        "Answer with the option letter and its text."
    )
    raw = ""
    error = ""
    for _ in range(5):
        try:
            raw = client.complete(
                "You are a careful detective-novel reader using ONLY the provided novel text.",
                prompt,
                max_tokens=3000,
            )
            if raw.strip():
                break
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:120]}"
    path.write_text(json.dumps({"answer": raw, "error": error}, ensure_ascii=False), encoding="utf-8")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=None, help="novel ids; default = all English novels")
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", default=str(OUT / "results_fullnovel.json"))
    args = parser.parse_args()

    cases = merge_cases()
    if args.novels:
        wanted = {str(n) for n in args.novels}
        cases = [c for c in cases if str(c["meta"]["novel_id"]) in wanted]
    total_questions = sum(len(c["questions"]) for c in cases)
    print(f"novels: {len(cases)}, questions: {total_questions}", flush=True)
    (OUT / "fullnovel_targets.json").write_text(
        json.dumps({"novels": [c["meta"]["novel_id"] for c in cases], "questions": total_questions}, ensure_ascii=False),
        encoding="utf-8",
    )

    client = UrllibClient("deepseek-v4-flash", reasoning_effort="low")
    tasks = []
    for case in cases:
        nid = str(case["meta"]["novel_id"])
        out_dir = OUT / "novels" / nid
        out_dir.mkdir(parents=True, exist_ok=True)
        questions = case["questions"]
        if args.max_questions:
            questions = questions[: args.max_questions]
        for qi, q in enumerate(questions):
            key = hashlib.sha1(f"fullnovel|{nid}|{qi}|{PROMPT_VER}".encode("utf-8")).hexdigest()[:10]
            tasks.append((case, nid, qi, q, out_dir, key))

    done = 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(answer_one, client, case, qi, q, out_dir, key): (nid, qi) for case, nid, qi, q, out_dir, key in tasks}
        for future in as_completed(futures):
            nid, qi = futures[future]
            try:
                future.result()
            except Exception as exc:
                print(f"[warn] {nid} Q{qi} failed: {type(exc).__name__}: {exc}", flush=True)
            done += 1
            if done % 20 == 0 or done == total_questions:
                elapsed = time.time() - started
                rate = done / elapsed
                eta = (total_questions - done) / rate / 60 if rate > 0 else None
                print(f"[progress] {done}/{total_questions} | {rate:.2f} q/min | eta {eta:.1f} min", flush=True)

    # assemble results JSON from caches
    rows = []
    for case in cases:
        nid = str(case["meta"]["novel_id"])
        out_dir = OUT / "novels" / nid
        for qi, q in enumerate(case["questions"]):
            key = hashlib.sha1(f"fullnovel|{nid}|{qi}|{PROMPT_VER}".encode("utf-8")).hexdigest()[:10]
            p = out_dir / f"fullnovel_{key}.json"
            if not p.exists():
                continue
            rows.append(
                {
                    "novel": nid,
                    "qid": q["qid"],
                    "question": q["question"],
                    "gold_text": q.get("gold_text"),
                    "gold_index": q.get("gold_index"),
                    "full": {"answer": json.loads(p.read_text(encoding="utf-8"))["answer"]},
                }
            )
    out = Path(args.out)
    out.write_text(json.dumps({"groups": ["full"], "results": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {len(rows)} rows -> {out}", flush=True)


if __name__ == "__main__":
    main()
