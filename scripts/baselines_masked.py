"""Masked variants of the basic baselines (tail / full-text), cutting at answer_position."""

from __future__ import annotations

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_four_datasets import OllamaClient, UrllibClient, load_cases, options_block  # noqa: E402

OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"
NOVELS = ["103", "104", "117", "100", "106", "108", "121"]
TAIL_CHARS = 50000


def merged() -> dict[str, dict]:
    by_novel: dict[str, dict] = {}
    for c in load_cases("detectiveqa"):
        nid = str(c["meta"]["novel_id"])
        if nid not in NOVELS:
            continue
        m = by_novel.setdefault(nid, {"text": c["text"], "questions": []})
        seen = {q["question"] for q in m["questions"]}
        for q in c["questions"]:
            if q["question"] not in seen:
                seen.add(q["question"])
                m["questions"].append(q)
    return by_novel


def answer(client, system, prompt, path, max_tokens=1500):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["answer"]
    raw = ""
    for _ in range(4):
        try:
            raw = client.complete(system, prompt, max_tokens=max_tokens)
            if raw.strip():
                break
        except Exception:
            pass
    path.write_text(json.dumps({"answer": raw}, ensure_ascii=False), encoding="utf-8")
    return raw


def main() -> None:
    qwen = OllamaClient("qwen2.5:7b", num_ctx=16384)
    ds = UrllibClient("deepseek-v4-flash", reasoning_effort="low")
    by_novel = merged()
    tasks = []
    for nid, info in by_novel.items():
        out_dir = OUT / "novels" / nid
        out_dir.mkdir(parents=True, exist_ok=True)
        for qi, q in enumerate(info["questions"]):
            mc = q.get("mask_char")
            if not isinstance(mc, int) or mc <= 0:
                continue
            prefix = info["text"][:mc]
            key = hashlib.sha1(f"{nid}|{qi}|masked|v1".encode("utf-8")).hexdigest()[:10]
            opt = options_block(q)
            tail = prefix[-TAIL_CHARS:]
            tail_prompt = f"Question: {q['question']}\n\n{opt}\n\nText (tail of the masked novel):\n{tail}\n\nAnswer with the option letter and its text."
            full_prompt = f"Question: {q['question']}\n\n{opt}\n\nNovel text:\n{prefix}\n\nAnswer with the option letter and its text."
            tasks.append(("tailm", nid, qi, q, out_dir / f"tailm_{key}.json", qwen, tail_prompt))
            tasks.append(("fullm", nid, qi, q, out_dir / f"fullm_{key}.json", ds, full_prompt))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(answer, c, "You are a careful detective-novel reader using ONLY the provided text.", p, path): (kind, nid, qi) for kind, nid, qi, q, path, c, p in tasks}
        done = 0
        for fut in as_completed(futures):
            kind, nid, qi = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                print(f"[warn] {nid} Q{qi} {kind}: {type(exc).__name__}", flush=True)
            done += 1
            if done % 20 == 0:
                print(f"[progress] {done}/{len(tasks)}", flush=True)
    print("all done", flush=True)


if __name__ == "__main__":
    main()
