"""Trace demo: show the agentic graph reasoning steps for a few questions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from detectiveqa_three_groups import answer_graph_agentic, norm_name  # noqa: E402
from eval_four_datasets import OllamaClient, load_cases  # noqa: E402
from novel_kg_studio.store import GraphStore  # noqa: E402

OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"


class LoggingClient:
    def __init__(self, inner):
        self.inner = inner

    def complete_json(self, system, user, *, max_tokens=None):
        print("\n>>> AGENT STEP (user prompt, first 500 chars):")
        print(user[:500].replace("\n", " | "))
        payload = self.inner.complete_json(system, user, max_tokens=max_tokens)
        print("<<< AGENT ACTION:", json.dumps(payload, ensure_ascii=False)[:300])
        return payload

    def complete(self, system, user, *, max_tokens=None):
        return self.inner.complete(system, user, max_tokens=max_tokens)


def main() -> None:
    by_novel: dict[str, dict] = {}
    for c in load_cases("detectiveqa"):
        nid = str(c["meta"]["novel_id"])
        merged = by_novel.setdefault(nid, {"text": c["text"], "questions": []})
        seen = {q["question"] for q in merged["questions"]}
        for q in c["questions"]:
            if q["question"] not in seen:
                seen.add(q["question"])
                merged["questions"].append(q)
    cases = by_novel
    for nid, question_sub in [("117", "Elena Marshall going to Fairy Bay"), ("104", "killed Mr. Maltravers")]:
        case = cases[nid]
        g = json.loads((OUT / "novels" / nid / "graph.json").read_text(encoding="utf-8"))
        store = GraphStore(g["nodes"], g["edges"])
        kept = []
        p = OUT / "novels" / nid / "pass1" / "kept.jsonl"
        if p.exists():
            kept = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        q = next(q for q in case["questions"] if question_sub in q["question"])
        mask_char = q.get("mask_char")
        mask = (mask_char / max(len(case["text"]), 1)) if mask_char else None
        print(f"\n========== {nid}: {q['question'][:60]} ==========")
        client = LoggingClient(OllamaClient("qwen2.5:7b", num_ctx=16384))
        ans = answer_graph_agentic(client, q, store, kept, mask, case["text"], OUT / "novels" / nid, max_steps=4)
        print("FINAL:", ans[:120])


if __name__ == "__main__":
    main()
