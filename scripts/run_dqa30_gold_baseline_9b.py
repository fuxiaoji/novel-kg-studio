"""Gold-only oracle baseline on the frozen 30-novel evaluation with qwen3.5:9b.

Definition (matches run_goldonly_20.py / goldonly_baseline.py): the prompt is the
question, the same four options as the frozen evaluation, and every nonnegative
official clue_position paragraph as evidence. No full novel, no final-answer
paragraph, no official reasoning, no retrieval.

The question set is aligned to the frozen 30-novel evaluation by reading the
234 qids from paper/generated/dqa30_per_question.csv, so every method in the
paper shares the exact same questions.

Protocol: qwen3.5:9b through Ollama, thinking disabled, num_ctx=16384, fixed
prompt.  Resumable per question (answer json written atomically and validated
on restart).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from build_c_next10_graphs import merged_cases  # noqa: E402
from c8_graph_passage import LETTERS, normalize_letter  # noqa: E402
from goldonly_baseline import load_anno, paragraph_map  # noqa: E402
from native_ollama_client import NativeOllamaNoThinkClient  # noqa: E402

VERSION = "dqa30-frozen-goldonly-9b-v1"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-csv", type=Path, default=ROOT / "paper" / "generated" / "dqa30_per_question.csv")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "goldonly_9b_30")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--variant", choices=["clue", "full"], default="clue",
                        help="clue = official clue_position paragraphs only; full = clue + answer_position paragraph (perfect-retrieval ceiling)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = list(csv.DictReader(args.frozen_csv.open(encoding="utf-8-sig", newline="")))
    total = len(rows)
    definition = ("question + same four options as frozen eval + every nonnegative official clue_position paragraph; no full novel, answer paragraph, or official reasoning"
                  if args.variant == "clue" else
                  "question + same four options as frozen eval + every nonnegative clue_position paragraph AND the official answer_position paragraph (perfect-retrieval ceiling)")
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "variant": args.variant,
        "model": args.model,
        "num_ctx": args.num_ctx,
        "questions": total,
        "definition": definition,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    novels = sorted({row["novel"] for row in rows}, key=lambda n: int(n))
    cases = merged_cases(novels)
    client = NativeOllamaNoThinkClient(args.model, num_ctx=args.num_ctx)

    done = correct = 0
    started = time.time()
    for novel in novels:
        annotations = {(r["source"], int(r["qi"])): r for r in load_anno(novel)}
        paragraphs = paragraph_map(novel)
        qs = {q["qid"]: q for q in cases[novel]["questions"]}
        for row in rows:
            if row["novel"] != novel:
                continue
            qi = int(row["qi"])
            qid = row["qid"]
            gold = row["gold"]
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            if path.exists():
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                    if old.get("version") == VERSION and normalize_letter(old.get("selected_letter")) in LETTERS:
                        done += 1
                        correct += bool(old["correct"])
                        continue
                except Exception:
                    pass
            question = qs.get(qid)
            if question is None:
                raise RuntimeError(f"question {qid} missing from merged_cases")
            tail = qid.removeprefix(f"detectiveqa_{novel}_")
            source, source_qi = tail.rsplit("_", 1)
            anno = annotations[(source, int(source_qi))]
            clue_positions = [int(p) for p in anno["clue_position"]
                              if isinstance(p, (int, float)) and int(p) >= 0 and int(p) in paragraphs]
            gold_positions = list(clue_positions)
            if args.variant == "full":
                answer_pos = int(anno.get("answer_position") or -1)
                if answer_pos >= 0 and answer_pos in paragraphs:
                    gold_positions.append(answer_pos)
            evidence = "\n\n".join(paragraphs[p] for p in gold_positions)
            options = "\n".join(f"{LETTERS[i]}. {text}" for i, text in enumerate(question["choices"][:4]))
            prompt = (
                f"Question: {question['question']}\n\n{options}\n\nEvidence:\n{evidence}\n\n"
                "Answer with the option letter and its text."
            )
            raw = ""
            for _ in range(4):
                try:
                    raw = client.complete(
                        "You are a careful detective-novel reader using ONLY the provided evidence.",
                        prompt,
                        max_tokens=600,
                    )
                    if raw.strip():
                        break
                except Exception:
                    raw = ""
                time.sleep(2.0)
            letter = normalize_letter(raw) or None
            result = {
                "version": VERSION,
                "novel": novel,
                "qi": qi,
                "qid": qid,
                "question": question["question"],
                "choices": question["choices"],
                "gold_letter": gold,
                "selected_letter": letter,
                "correct": letter == gold,
                "answer": raw,
                "clue_positions": clue_positions,
                "evidence_chars": len(evidence),
                "model": args.model,
                "elapsed_seconds": round(time.time() - started, 1),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            done += 1
            correct += bool(result["correct"])
            print(f"[{done}/{total}] gold9b/{novel}/q{qi} -> {letter} (correct={result['correct']})", flush=True)
    summary = {"version": VERSION, "correct": correct, "total": done, "accuracy": correct / done if done else 0.0}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
