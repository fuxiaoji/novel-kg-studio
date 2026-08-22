"""Baseline experiment: feed ONLY the masked novel text to the LLM, compare with the RAG pipeline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.schema import norm_text

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"

# Per-question gold: answer entity + canonical clue terms (with translation variants found in the graph).
GOLD = {
    "How did the killer leave the scene?": {"answer": "window", "terms": ["window", "climb"], "official": True},
    "Who killed Mr. Renault?": {"answer": "Marte Daubreuil", "terms": ["marte", "daubreuil", "malt", "dobré", "dobrova", "marthe", "dobroslava"]},
    "What weapon was used in the murder?": {"answer": "paper knife", "terms": ["paper knife", "paperknife", "paper-cutter"]},
    "Who was the accomplice of the killer?": {"answer": "Madame Daubreuil", "terms": ["berodsky", "berody", "daubreuil"]},
    "What did the killer use to cover the footprints?": {"answer": "rake", "terms": ["rake"]},
    "Who was the woman Jack Renault loved?": {"answer": "Bella Duveen", "terms": ["bella", "duvain", "duveen", "duvine"]},
    "Who is the millionaire found dead at the villa?": {"answer": "Mr. Renault", "terms": ["renault", "reno"]},
}

MAX_INPUT_CHARS = 20000


def masked_text(kept: list[dict], mask: float) -> str:
    visible = [row for row in kept if float(row["text_position"]) <= mask]
    text = "\n".join(row["text"] for row in sorted(visible, key=lambda r: r["seq"]))
    if len(text) <= MAX_INPUT_CHARS:
        return text
    head_chars = int(MAX_INPUT_CHARS * 0.6)
    tail_chars = MAX_INPUT_CHARS - head_chars
    return f"{text[:head_chars]}\n...[middle omitted]...\n{text[-tail_chars:]}"


def hit(answer: str, terms: list[str]) -> bool:
    lowered = str(answer or "").lower()
    return any(term in lowered for term in terms)


def coverage_from_names(by_name, by_id, names: list[str], terms: list[str]) -> float:
    found: set[str] = set()
    for name in names:
        node_id = by_name.get(norm_text(name))
        node = by_id.get(node_id) if node_id else None
        if node is None:
            continue
        text = " ".join([node["name"], *node.get("aliases", []), *node.get("evidence", [])]).lower()
        for term in terms:
            if term in text:
                found.add(term)
    return len(found) / max(len(terms), 1)


def main() -> None:
    config = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rag_rows = json.loads((OUT / "question_set_results.json").read_text(encoding="utf-8"))
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    by_name = {}
    by_id = {n["id"]: n for n in graph["nodes"]}
    for node in graph["nodes"]:
        by_name.setdefault(norm_text(node["name"]), node["id"])

    client = LLMClient(model="deepseek-chat", temperature=0.0, max_tokens=1200, retries=3)
    rows = []
    for item in config.get("question_set") or []:
        question = str(item["question"])
        mask = float(item.get("mask", 1.0))
        gold = GOLD.get(question, {"answer": "?", "terms": []})
        cache_key = hashlib.sha1(f"{question}|{mask}".encode("utf-8")).hexdigest()[:12]
        cache_path = OUT / "masked_qa" / f"{cache_key}.json"
        cached = load_json(cache_path)
        if cached:
            answer = cached["answer"]
        else:
            text = masked_text(kept, mask)
            payload = client.complete_json(
                "You are a careful detective-novel reader. Use ONLY the provided story text.",
                (
                    f"You are reading the novel up to {mask:.0%} of the book; the rest is hidden.\n\n"
                    f"Story text:\n{text}\n\nQuestion: {question}\n\n"
                    "Answer with the most likely answer based ONLY on the visible text. "
                    "If it cannot be determined, say so and give your best guess.\n"
                    'Return strict JSON only: {"answer": "..."}'
                ),
            )
            answer = str(payload.get("answer") or "") if isinstance(payload, dict) else str(payload)
            save_json(cache_path, {"answer": answer})

        rag_row = next((r for r in rag_rows if r["question"] == question), None)
        rag_answer = rag_row.get("answer", "") if rag_row else ""
        rag_hit = hit(rag_answer, gold["terms"])
        text_hit = hit(answer, gold["terms"])
        rag_cov_first = coverage_from_names(by_name, by_id, rag_row["first_order"], gold["terms"]) if rag_row else 0.0
        rag_cov_second = coverage_from_names(by_name, by_id, rag_row["first_order"] + rag_row["second_order"], gold["terms"]) if rag_row else 0.0
        rows.append(
            {
                "question": question,
                "mask": mask,
                "gold_answer": gold["answer"],
                "masked_text_answer": answer,
                "masked_text_hit": text_hit,
                "rag_answer": rag_answer,
                "rag_hit": rag_hit,
                "rag_coverage_first": round(rag_cov_first, 3),
                "rag_coverage_first_second": round(rag_cov_second, 3),
            }
        )
        print(
            f"[{question[:40]}] mask={mask:.2f} | 文本基线hit={text_hit} | RAG hit={rag_hit} "
            f"| RAG金标覆盖 一阶={rag_cov_first:.2f} 一阶+二阶={rag_cov_second:.2f}"
        )

    save_json(OUT / "masked_qa_results.json", rows)
    full = [r for r in rows if r["mask"] >= 0.99]
    text_acc = sum(r["masked_text_hit"] for r in full) / max(len(full), 1)
    rag_acc = sum(r["rag_hit"] for r in full) / max(len(full), 1)
    print()
    print(f"== 全文本题（mask=1.0，{len(full)} 题）==")
    print(f"只给掩码后小说文本的 LLM 正确率: {text_acc:.0%}")
    print(f"RAG 管线正确率: {rag_acc:.0%}")
    print("saved:", OUT / "masked_qa_results.json")


if __name__ == "__main__":
    main()
