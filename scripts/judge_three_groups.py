"""External DeepSeek judge for the three-group DetectiveQA results."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"


def judge(client, question: str, gold: str, answer: str, cache_dir: Path, key: str) -> tuple[bool, str]:
    path = cache_dir / f"j_{key}.json"
    cached = load_json(path)
    if cached:
        return bool(cached["correct"]), str(cached["note"])
    payload = client.complete_json(
        "You are a strict but fair answer judge for a detective-novel QA benchmark.",
        (
            f"Question: {question}\nGold answer: {gold}\nModel answer: {answer}\n"
            'Return strict JSON only: {"correct": true/false, "note": "one short reason"}'
        ),
    )
    verdict = {
        "correct": bool(payload.get("correct")) if isinstance(payload, dict) else False,
        "note": str(payload.get("note") or "") if isinstance(payload, dict) else "",
    }
    save_json(path, verdict)
    return verdict["correct"], verdict["note"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--out", dest="dst", required=True)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--reasoning_effort", default="low")
    args = parser.parse_args()
    client = LLMClient(model=args.model, temperature=0.0, max_tokens=2000, retries=3, reasoning_effort=args.reasoning_effort)
    cache_dir = OUT / "judge_cache"
    data = json.loads(Path(args.src).read_text(encoding="utf-8"))
    groups = data.get("groups") or []
    counts = {g: {"correct": 0, "total": 0} for g in groups}
    for row in data["results"]:
        for g in groups:
            if g not in row:
                continue
            answer = row[g]["answer"]
            qid = row.get("qid") or f"{row.get('novel', '?')}_{row.get('source', '?')}_{row.get('qi', '?')}"
            key = hashlib.sha1(f"{qid}|{g}|{answer}".encode("utf-8")).hexdigest()[:12]
            correct, note = judge(client, row["question"], row.get("gold_text") or "", answer, cache_dir, key)
            row[g]["correct"] = correct
            row[g]["note"] = note
            counts[g]["total"] += 1
            counts[g]["correct"] += 1 if correct else 0
    data["summary"] = {g: f"{counts[g]['correct']}/{counts[g]['total']}" for g in groups}
    Path(args.dst).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for g in groups:
        print(f"{g}: {counts[g]['correct']}/{counts[g]['total']}")


if __name__ == "__main__":
    main()
