"""C23: local C15 graph reader under a cyclic option permutation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from analyze_dense_retrieval import chunk_embeddings  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c8_graph_passage import C8Context, LETTERS  # noqa: E402
from native_ollama_client import NativeOllamaNoThinkClient  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, graph_path  # noqa: E402
from run_local_smallmodel_pilot import answer_graph  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
VERSION = "c23-local-c15-cyclic-option-order-v1"
PERMUTATION = [1, 2, 3, 0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--out", type=Path, default=BASE / "dqa_local_c23_cyclic20")
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    client = NativeOllamaNoThinkClient(args.model)
    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        for qi, q in enumerate(case["questions"]):
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            if path.exists():
                continue
            permuted = {**q, "choices": [q["choices"][index] for index in PERMUTATION]}
            result = answer_graph(client, permuted, ctx, matrix)
            permuted_letter = result.get("selected_letter")
            if not isinstance(permuted_letter, str) or permuted_letter not in LETTERS:
                retry = answer_graph(client, permuted, ctx, matrix)
                if isinstance(retry.get("selected_letter"), str) and retry["selected_letter"] in LETTERS:
                    result = retry
                    permuted_letter = retry["selected_letter"]
            mapped = LETTERS[PERMUTATION[LETTERS.index(permuted_letter)]] if isinstance(permuted_letter, str) and permuted_letter in LETTERS else None
            gold = LETTERS[q["gold_index"]]
            result.update({"version": VERSION, "model": args.model, "thinking": "disabled", "external_api": False, "mask": "unmasked", "permutation": PERMUTATION, "permuted_letter": permuted_letter, "selected_letter": mapped, "novel": novel, "batch": "first10" if novel in FIRST10 else "second10", "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": gold, "correct": mapped == gold})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[{novel}/q{qi}] perm={permuted_letter} mapped={mapped} gold={gold}", flush=True)


if __name__ == "__main__":
    main()
