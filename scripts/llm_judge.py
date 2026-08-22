"""LLM-as-judge: judge v2 answers against gold, tolerant to translation variants."""

from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"

SYNONYMS = {
    "Who killed Mr. Renault?": ["Marte", "Daubreuil", "Malt", "Dobroil", "Dobrowolski", "Miss Marte", "Marthe", "Marte Dobrow"],
    "Who was the accomplice of the killer?": ["Madame Daubreuil", "Berodie", "Berodsky", "Berodich", "Berody", "Dobler", "Dobor", "Dobroff", "Dobrel"],
    "Who was the woman Jack Renault loved?": ["Bella", "Duveen", "DuVain", "DuVine", "Duval"],
    "Who is the millionaire found dead at the villa?": ["Renault", "Renauld", "Reno"],
    "How did the killer leave the scene?": ["window"],
    "What weapon was used in the murder?": ["paper knife", "paperknife", "paper cutter"],
    "What did the killer use to cover the footprints?": ["rake"],
}

JUDGE_PROMPT = """Judge whether the model's answer is correct for a detective-novel question.
Question: {question}
Gold answer: {gold}
Accepted variants: {synonyms}
Model answer: {answer}
Return strict JSON only: {{"correct": true/false, "note": "one short reason"}}"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="deepseek-chat")
    args = parser.parse_args()
    rows = json.loads((OUT / "question_set_results.json").read_text(encoding="utf-8"))
    client = LLMClient(model=args.model, temperature=0.0, max_tokens=500, retries=3)
    judged = []
    for row in rows:
        question = row["question"]
        key = hashlib.sha1(f"{question}|{row['answer']}".encode("utf-8")).hexdigest()[:12]
        path = OUT / "judge" / f"{key}.json"
        cached = load_json(path)
        if cached:
            verdict = cached
        else:
            payload = client.complete_json(
                "You are a strict but fair answer judge for a detective-novel QA benchmark.",
                JUDGE_PROMPT.format(
                    question=question,
                    gold=row["gold_answer"],
                    synonyms=", ".join(SYNONYMS.get(question, [row["gold_answer"]])),
                    answer=row["answer"],
                ),
            )
            verdict = {
                "correct": bool(payload.get("correct")) if isinstance(payload, dict) else False,
                "note": str(payload.get("note") or "") if isinstance(payload, dict) else "",
            }
            save_json(path, verdict)
        judged.append({**row, "judged_correct": verdict["correct"], "judge_note": verdict["note"]})
        print(f"[{question[:42]}] mask={row['mask']:.2f} 裁判={'对' if verdict['correct'] else '错'} | {verdict['note'][:90]}")
    save_json(OUT / "question_set_judged.json", judged)
    full = [r for r in judged if r["mask"] >= 0.99]
    acc = sum(1 for r in full if r["judged_correct"]) / max(len(full), 1)
    all_acc = sum(1 for r in judged if r["judged_correct"]) / max(len(judged), 1)
    print(f"\n全文本题裁判正确率: {acc:.0%} ({sum(1 for r in full if r['judged_correct'])}/{len(full)})")
    print(f"全部 7 题裁判正确率: {all_acc:.0%}")
    print("saved:", OUT / "question_set_judged.json")


if __name__ == "__main__":
    main()
