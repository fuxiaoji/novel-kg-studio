"""Debias-controlled gold-only baseline on the frozen 30-novel evaluation.

Same definition as the D-anchored clue-only run (question + same four options +
every nonnegative official clue_position paragraph as evidence; no answer
paragraph), but with two orthogonal prompt interventions to eliminate the
last-option (D) anchoring found in goldonly_9b_30:

  V1  evidence BEFORE options, original option order   (isolates structure effect)
  V2  evidence BEFORE options, per-question shuffled options  (FAIR CEILING)
  V3  options FIRST, per-question shuffled options      (isolates position effect)

Together with the existing options-first + original-order run they complete a
2x2 grid that separates "trailing evidence block" anchoring from a general
position bias.

Shuffle is deterministic per qid (sha256-seeded), so runs are reproducible and
resumable per (variant, question).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
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

VERSION = "dqa30-frozen-goldonly-fair-v3"
VARIANTS = ("v1_evid_first", "v2_evid_first_shuf", "v3_opt_first_shuf")


def shuffle_perm(seed: str) -> list[int]:
    """Deterministic 4-element permutation from a qid seed."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    perm = list(range(4))
    # Fisher-Yates with bytes from the digest
    for i in range(3, 0, -1):
        j = digest[i] % (i + 1)
        perm[i], perm[j] = perm[j], perm[i]
    return perm


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-csv", type=Path, default=ROOT / "paper" / "generated" / "dqa30_per_question.csv")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa30_attention" / "goldonly_9b_30_fair")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=VARIANTS)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = list(csv.DictReader(args.frozen_csv.open(encoding="utf-8-sig", newline="")))
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": VERSION,
        "model": args.model,
        "num_ctx": args.num_ctx,
        "questions": len(rows),
        "variants": args.variants,
        "definition": "question + same four options + every nonnegative official clue_position paragraph; no answer paragraph",
        "interventions": {
            "v1_evid_first": "evidence block BEFORE the options; original option order",
            "v2_evid_first_shuf": "evidence BEFORE options; per-qid sha256-seeded shuffled options (fair ceiling)",
            "v3_opt_first_shuf": "options FIRST; per-qid sha256-seeded shuffled options",
        },
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    novels = sorted({row["novel"] for row in rows}, key=lambda n: int(n))
    cases = merged_cases(novels)
    client = NativeOllamaNoThinkClient(args.model, num_ctx=args.num_ctx)

    per_variant = {v: {"done": 0, "correct": 0} for v in args.variants}
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
            gold_letter = row["gold"]
            gold_index = ord(gold_letter) - ord("A")
            question = qs.get(qid)
            if question is None:
                raise RuntimeError(f"question {qid} missing from merged_cases")
            tail = qid.removeprefix(f"detectiveqa_{novel}_")
            source, source_qi = tail.rsplit("_", 1)
            anno = annotations[(source, int(source_qi))]
            clue_positions = [int(p) for p in anno["clue_position"]
                              if isinstance(p, (int, float)) and int(p) >= 0 and int(p) in paragraphs]
            evidence = "\n\n".join(paragraphs[p] for p in clue_positions)
            choices = question["choices"][:4]
            perm = shuffle_perm(qid)

            for variant in args.variants:
                path = args.out / variant / "answers" / novel / f"q{qi:02d}.json"
                if path.exists():
                    try:
                        old = json.loads(path.read_text(encoding="utf-8"))
                        if old.get("version") == VERSION and old.get("variant") == variant:
                            per_variant[variant]["done"] += 1
                            per_variant[variant]["correct"] += bool(old["correct"])
                            continue
                    except Exception:
                        pass
                if variant.endswith("_shuf"):
                    shown = [choices[i] for i in perm]
                    shown_gold = perm.index(gold_index)  # position of the correct option in the shown order
                else:
                    shown = list(choices)
                    shown_gold = gold_index
                options = "\n".join(f"{LETTERS[i]}. {text}" for i, text in enumerate(shown))
                # Force a terse final line: evidence-first makes qwen3.5:9b verbose otherwise,
                # which is both slow and not comparable to the terse style of the original run.
                if variant.startswith("v1_") or variant.startswith("v2_"):
                    prompt = (f"Question: {question['question']}\n\nEvidence:\n{evidence}\n\n"
                              f"{options}\n\nDo not explain. Answer with exactly one line: Answer: [letter]")
                else:
                    prompt = (f"Question: {question['question']}\n\n{options}\n\nEvidence:\n{evidence}\n\n"
                              "Do not explain. Answer with exactly one line: Answer: [letter]")
                raw = ""
                for _ in range(4):
                    try:
                        raw = client.complete(
                            "You are a careful detective-novel reader using ONLY the provided evidence.",
                            prompt, max_tokens=1200,
                        )
                        if raw.strip():
                            break
                    except Exception:
                        raw = ""
                    time.sleep(2.0)
                letter = normalize_letter(raw)
                correct = bool(letter and letter == LETTERS[shown_gold])
                result = {
                    "version": VERSION,
                    "variant": variant,
                    "novel": novel,
                    "qi": qi,
                    "qid": qid,
                    "question": question["question"],
                    "choices": choices,
                    "shown_order": shown,
                    "perm": perm,
                    "gold_letter": gold_letter,
                    "shown_gold": LETTERS[shown_gold],
                    "selected_letter": letter,
                    "correct": correct,
                    "answer": raw,
                    "clue_positions": clue_positions,
                    "evidence_chars": len(evidence),
                    "model": args.model,
                    "elapsed_seconds": round(time.time() - started, 1),
                }
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
                per_variant[variant]["done"] += 1
                per_variant[variant]["correct"] += int(correct)
                print(f"[{variant}] {novel}/q{qi:02d} -> {letter} (gold={gold_letter}, correct={correct})", flush=True)

    summary = {v: {**per_variant[v], "accuracy": per_variant[v]["correct"] / per_variant[v]["done"]} for v in args.variants}
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
