"""Re-judge saved answers with an external judge (DeepSeek) instead of the answering model."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient, extract_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "four_datasets"


def judge_letter(client, question: str, gold: str, answer: str, cache_dir: Path, key: str) -> tuple[bool, str]:
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


def parse_pair(ans: str) -> tuple[int, int] | None:
    try:
        p = extract_json(ans)
        return int(p.get("evidence", -1)), int(p.get("testimony", -1))
    except Exception:
        m = re.search(r'"evidence"\s*:\s*(\d+).*?"testimony"\s*:\s*(\d+)', ans, re.S)
        return (int(m.group(1)), int(m.group(2))) if m else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", required=True)
    parser.add_argument("--out", dest="dst", required=True)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--reasoning_effort", default="low")
    args = parser.parse_args()

    client = LLMClient(
        model=args.model,
        temperature=0.0,
        max_tokens=2000,
        retries=3,
        reasoning_effort=args.reasoning_effort,
    )
    cache_dir = OUT / "rejudge"
    data = json.loads(Path(args.src).read_text(encoding="utf-8"))
    for result in data["results"]:
        for row in result["rows"]:
            for method in ("full_text", "graph"):
                if method not in row:
                    continue
                item = row[method]
                answer = str(item.get("answer") or "")
                if row.get("gold_pairs") is not None:
                    gold = [tuple(p) for p in row["gold_pairs"]]
                    ev, te = parse_pair(answer) or (-2, -2)
                    item["pair"] = (ev, te) in gold
                    item["evidence"] = ev in {e for e, _ in gold}
                    item["testimony"] = te in {t for _, t in gold}
                    item["note"] = f"pair={item['pair']} ev={item['evidence']} te={item['testimony']}"
                    continue
                key = hashlib.sha1(f"{row['qid']}|{method}|{answer}".encode("utf-8")).hexdigest()[:12]
                correct, note = judge_letter(client, row["question"], row.get("gold_text") or "", answer, cache_dir, key)
                item["correct"] = correct
                item["note"] = note
    # recompute summaries
    summaries = {}
    for result in data["results"]:
        ds = result["dataset"]
        s = summaries.setdefault(ds, {"dataset": ds, "full_text": {"correct": 0, "total": 0}, "graph": {"correct": 0, "total": 0}})
        for row in result["rows"]:
            for method in ("full_text", "graph"):
                if method not in row:
                    continue
                item = row[method]
                ok = item.get("correct", item.get("pair", False))
                s[method]["total"] += 1
                s[method]["correct"] += 1 if ok else 0
    data["summaries"] = list(summaries.values())
    Path(args.dst).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for s in data["summaries"]:
        print(f"{s['dataset']}: full {s['full_text']['correct']}/{s['full_text']['total']} | graph {s['graph']['correct']}/{s['graph']['total']}")


if __name__ == "__main__":
    main()
