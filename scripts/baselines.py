"""Baseline comparison WITH options: masked-text / chunk-BM25 / sentence-BM25 / full-text vs graph RAG."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.chunking import chunk_text
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.store.bm25 import BM25Index
from llm_judge import JUDGE_PROMPT, SYNONYMS
from masked_text_qa import GOLD, hit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"
MAX_INPUT_CHARS = 20000
DECISIVE_PHRASES = ["climbed out of the window", "left the front door half-open"]


def masked_text(kept: list[dict], mask: float) -> str:
    visible = [row for row in kept if float(row["text_position"]) <= mask]
    text = "\n".join(row["text"] for row in sorted(visible, key=lambda r: r["seq"]))
    if len(text) <= MAX_INPUT_CHARS:
        return text
    head_chars = int(MAX_INPUT_CHARS * 0.6)
    tail_chars = MAX_INPUT_CHARS - head_chars
    return f"{text[:head_chars]}\n...[middle omitted]...\n{text[-tail_chars:]}"


def masked_full(kept: list[dict], mask: float) -> str:
    visible = [row for row in kept if float(row["text_position"]) <= mask]
    return "\n".join(row["text"] for row in sorted(visible, key=lambda r: r["seq"]))


def options_block(item: dict) -> str:
    choices = item.get("choices") or []
    if not choices:
        return ""
    return "Options:\n" + "\n".join(f"{chr(65 + i)}. {str(c).strip()}" for i, c in enumerate(choices))


def answer_plain(client, method: str, question: str, mask: float, context: str, opt_block: str, model: str, mode_tag: str = "") -> str:
    opt_hash = hashlib.sha1(opt_block.encode("utf-8")).hexdigest()[:6]
    key = hashlib.sha1(f"{method}|{question}|{mask}|{opt_hash}|{model}|{mode_tag}".encode("utf-8")).hexdigest()[:12]
    path = OUT / "baselines" / f"{key}.json"
    cached = load_json(path)
    if cached:
        return cached["answer"]
    raw = ""
    error = ""
    for _ in range(3):
        try:
            raw = client.complete(
                "You are a careful detective-novel reader using ONLY the provided text.",
                (
                    f"You are a reader at {mask:.0%} of the novel; the rest is hidden.\n"
                    f"Question: {question}\n\n{opt_block}\n\nProvided text:\n{context}\n\n"
                    "Answer with the option letter and its text (e.g. 'C. Through the window') when options are given; "
                    "otherwise answer in one short line. If undecidable, say 'unknown'."
                ),
                max_tokens=3000,
            )
            if raw.strip():
                break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    save_json(path, {"answer": raw, "error": error})
    return raw


def judge(client, question: str, gold_answer: str, model_answer: str) -> tuple[bool, str]:
    key = hashlib.sha1(f"{question}|{model_answer}".encode("utf-8")).hexdigest()[:12]
    path = OUT / "baseline_judge" / f"{key}.json"
    cached = load_json(path)
    if cached:
        return bool(cached["correct"]), str(cached["note"])
    payload = client.complete_json(
        "You are a strict but fair answer judge for a detective-novel QA benchmark.",
        JUDGE_PROMPT.format(
            question=question,
            gold=gold_answer,
            synonyms=", ".join(SYNONYMS.get(question, [gold_answer])),
            answer=model_answer,
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
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--reasoning_effort", default=None)
    parser.add_argument("--thinking", default=None)
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    client = LLMClient(
        model=args.model,
        temperature=0.0,
        max_tokens=3000,
        retries=3,
        reasoning_effort=args.reasoning_effort,
        thinking=args.thinking,
    )
    mode_tag = f"{args.thinking or ''}|{args.reasoning_effort or ''}"
    graph_rows = json.loads((OUT / "question_set_judged.json").read_text(encoding="utf-8"))
    graph_by_q = {r["question"]: r for r in graph_rows}

    results = []
    for item in config.get("question_set") or []:
        question = str(item["question"])
        mask = float(item.get("mask", 1.0))
        gold = GOLD.get(question, {"answer": "?", "terms": []})
        opt_block = options_block(item)
        text20k = masked_text(kept, mask)
        full_text = masked_full(kept, mask)

        chunk_context = ""
        chunks = chunk_text(text20k, size=1500, overlap=100)
        if chunks:
            index = BM25Index([c.text for c in chunks])
            scores = index.score(question)
            order = scores.argsort()[::-1]
            top = [chunks[i].text for i in order if scores[i] > 0][:6]
            chunk_context = "\n".join(top)

        sent_context = ""
        visible = sorted([r for r in kept if float(r["text_position"]) <= mask], key=lambda r: r["seq"])
        if visible:
            index = BM25Index([r["text"] for r in visible])
            scores = index.score(question)
            order = scores.argsort()[::-1]
            top = [visible[i]["text"] for i in order if scores[i] > 0][:8]
            sent_context = "\n".join(top)

        contexts = {
            "masked_text": text20k,
            "chunk_bm25": chunk_context,
            "sentence_bm25": sent_context,
            "full_text": full_text,
        }
        row = {"question": question, "mask": mask, "gold_answer": gold["answer"], "choices": item.get("choices") or []}
        for method, context in contexts.items():
            ans = answer_plain(client, method, question, mask, context, opt_block, args.model, mode_tag)
            is_hit = hit(ans, gold["terms"])
            correct, note = judge(client, question, gold["answer"], ans)
            row[method] = {"answer": ans, "hit": is_hit, "judged": correct, "note": note}
            print(f"[{method}] {question[:38]} mask={mask:.2f} judged={'对' if correct else '错'} | {ans[:70]}")
        expected_letter = ""
        for idx, choice in enumerate(item.get("choices") or []):
            lowered = str(choice).lower()
            if any(term in lowered for term in gold["terms"]):
                expected_letter = chr(65 + idx)
                break
        row["expected_letter"] = expected_letter
        row["answer_in_context"] = {
            "masked_text": any(p in text20k for p in DECISIVE_PHRASES),
            "chunk_bm25": any(p in chunk_context for p in DECISIVE_PHRASES),
            "sentence_bm25": any(p in sent_context for p in DECISIVE_PHRASES),
            "full_text": any(p in full_text for p in DECISIVE_PHRASES),
        }
        graph_row = graph_by_q.get(question)
        row["graph_rag"] = {"judged": bool(graph_row["judged_correct"]) if graph_row else False, "answer": (graph_row or {}).get("answer", "")}
        results.append(row)

    save_json(OUT / "baselines_results.json", results)
    methods = ["masked_text", "chunk_bm25", "sentence_bm25", "full_text", "graph_rag"]
    full = [r for r in results if r["mask"] >= 0.99]
    print("\n== 全文本题正确率（LLM 裁判，带选项）==")
    for method in methods:
        acc = sum(1 for r in full if r[method]["judged"]) / max(len(full), 1)
        print(f"  {method}: {acc:.0%} ({sum(1 for r in full if r[method]['judged'])}/{len(full)})")
    print("saved:", OUT / "baselines_results.json")


if __name__ == "__main__":
    main()
