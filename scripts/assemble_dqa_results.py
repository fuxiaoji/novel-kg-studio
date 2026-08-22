"""Assemble completed DetectiveQA answers (three groups + full-novel baseline) into one results file."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_four_datasets import load_cases  # noqa: E402

OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"
NOVEL_DIR = OUT / "novels"
GROUPS = [("graph_", "graph"), ("tail_", "tail"), ("comp_", "compress"), ("fullnovel_", "full")]


def main() -> None:
    by_novel: dict[str, dict] = {}
    for c in load_cases("detectiveqa"):
        nid = str(c["meta"]["novel_id"])
        merged = by_novel.setdefault(nid, {"questions": []})
        seen = {q["question"] for q in merged["questions"]}
        for q in c["questions"]:
            if q["question"] not in seen:
                seen.add(q["question"])
                merged["questions"].append(q)
    rows = []
    for nid in sorted(by_novel, key=int):
        d = NOVEL_DIR / nid
        if not d.exists():
            continue
        for qi, q in enumerate(by_novel[nid]["questions"]):
            key3 = hashlib.sha1(f"{nid}|{qi}|v1".encode("utf-8")).hexdigest()[:10]
            keyf = hashlib.sha1(f"fullnovel|{nid}|{qi}|v1".encode("utf-8")).hexdigest()[:10]
            row = {
                "novel": nid,
                "qid": q["qid"],
                "question": q["question"],
                "gold_text": q.get("gold_text"),
                "gold_index": q.get("gold_index"),
                "mask_char": q.get("mask_char"),
            }
            for prefix, g in GROUPS:
                p = d / f"{prefix}{key3 if g != 'full' else keyf}.json"
                if p.exists():
                    row[g] = {"answer": json.loads(p.read_text(encoding="utf-8"))["answer"]}
            if len(row) > 6:
                rows.append(row)
    out = OUT / "results_assembled.json"
    out.write_text(json.dumps({"groups": ["graph", "tail", "compress", "full"], "results": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {g: 0 for _, g in GROUPS}
    for r in rows:
        for _, g in GROUPS:
            counts[g] += 1 if g in r else 0
    print(f"saved {len(rows)} rows | counts: {counts}")


if __name__ == "__main__":
    main()
