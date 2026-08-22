"""Oracle: can the LLM reason to the answer from ONLY the gold source text (no answer, no reasoning)?"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.schema import norm_text
from masked_text_qa import GOLD

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"

ORACLE_PROMPT = """You are a detective-novel reader. Below are source passages from the novel that have been
selected as relevant to the question. Use ONLY these passages.

Question: {question}

Passages:
{passages}

Answer with the most likely answer based only on these passages; state uncertainty if undecidable.
Return strict JSON only: {{"answer": "..."}}"""


def main() -> None:
    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    import yaml

    config = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    client = LLMClient(model="deepseek-chat", temperature=0.0, max_tokens=800, retries=3)
    rows = []
    for item in config.get("question_set") or []:
        question = str(item["question"])
        mask = float(item.get("mask", 1.0))
        gold = GOLD.get(question, {"answer": "?", "terms": []})
        visible = [row for row in kept if float(row["text_position"]) <= mask]
        passages = []
        seen = set()
        for row in sorted(visible, key=lambda r: r["seq"]):
            lowered = norm_text(row["text"])
            if any(term in lowered for term in gold["terms"]):
                if row["seq"] not in seen:
                    seen.add(row["seq"])
                    passages.append(row["text"])
        passages = passages[:6]
        key = hashlib.sha1(f"oracle|{question}|{mask}".encode("utf-8")).hexdigest()[:12]
        path = OUT / "oracle_source" / f"{key}.json"
        cached = load_json(path)
        if cached:
            answer = cached["answer"]
        else:
            payload = client.complete_json(
                "You are an expert detective-novel reader.",
                ORACLE_PROMPT.format(question=question, passages="\n".join(f"- {p}" for p in passages)),
            )
            answer = str(payload.get("answer") or "") if isinstance(payload, dict) else str(payload)
            save_json(path, {"answer": answer})
        lowered = str(answer or "").lower()
        hit = any(term in lowered for term in gold["terms"])
        rows.append({"question": question, "mask": mask, "gold_answer": gold["answer"], "passage_count": len(passages), "answer": answer, "hit": hit})
        print(f"[{question[:40]}] mask={mask:.2f} 段落={len(passages)} 命中={hit} | {answer[:120]}")
    save_json(OUT / "gold_source_oracle.json", rows)
    full = [r for r in rows if r["mask"] >= 0.99]
    print(f"\n金标源文本 oracle：全文本题 {sum(1 for r in full if r['hit'])}/{len(full)}，全部 {sum(1 for r in rows if r['hit'])}/{len(rows)}")


if __name__ == "__main__":
    main()
