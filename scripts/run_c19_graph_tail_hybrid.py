"""C19: one-call local reader combining graph-retrieved passages with a short novel tail."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from build_c_next10_graphs import merged_cases  # noqa: E402
from c8_graph_passage import LETTERS, normalize_letter  # noqa: E402
from native_ollama_client import NativeOllamaNoThinkClient  # noqa: E402
from run_c8_20 import FIRST10, NOVELS  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
VERSION = "c19-local-graph-passages-plus-short-tail-v1"
TAIL_CHARS = 12_000
PASSAGE_CHARS = 18_000


def load_graph_rows() -> dict[str, dict[str, Any]]:
    rows = {}
    for path in (BASE / "dqa_qwen35_c15_20" / "answers" / "graph").rglob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        rows[row["qid"]] = row
    return rows


def evidence_text(row: dict[str, Any]) -> str:
    rendered = []
    used = 0
    for chunk in row.get("retrieval", {}).get("chunks", []):
        text = str(chunk.get("text", ""))
        labels = ",".join(sorted(set(chunk.get("for_options", []))))
        block = f"[{chunk.get('id','')} | retrieved for candidate(s) {labels}]\n{text}"
        if used + len(block) > PASSAGE_CHARS:
            break
        rendered.append(block)
        used += len(block)
    return "\n\n".join(rendered)


def options_text(q: dict[str, Any]) -> str:
    return "\n".join(f"{LETTERS[index]}. {choice}" for index, choice in enumerate(q["choices"][:4]))


def answer(client: NativeOllamaNoThinkClient, q: dict[str, Any], text: str, graph_row: dict[str, Any]) -> dict[str, Any]:
    prompt = (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options_text(q)}\n\n"
        f"GRAPH-NAVIGATED ORIGINAL PASSAGES\n{evidence_text(graph_row)}\n\n"
        f"SHORT FINAL NOVEL WINDOW\n{text[-TAIL_CHARS:]}\n\n"
        "Answer from the original novel text above. The retrieval labels only show which candidate caused retrieval; they are not "
        "votes or evidence by themselves. Compare every option. Prefer explicit final revelations over earlier hypotheses, accusations, "
        "and red herrings. Resolve translated aliases by event semantics. Missing evidence is unknown, not contradiction. For identity "
        "questions distinguish real identity, disguise, victim, and killer. Obey NOT/EXCEPT wording. Return JSON only: "
        '{"selected_letter":"A|B|C|D","evidence_region":"retrieved|tail|both","decisive_evidence":"brief"}'
    )
    raw = client.complete_json("Read compact graph-navigated passages plus the novel ending. No chain of thought.", prompt, max_tokens=180)
    return {"selected_letter": normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw), "raw": raw}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--out", type=Path, default=BASE / "dqa_local_c19_hybrid20")
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    graph_rows = load_graph_rows()
    client = NativeOllamaNoThinkClient(args.model)
    for novel in args.novels:
        case = cases[novel]
        for qi, q in enumerate(case["questions"][: args.max_questions or None]):
            path = args.out / "answers" / args.model.replace(":", "_") / novel / f"q{qi:02d}.json"
            if path.exists():
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                    if old.get("version") == VERSION and old.get("selected_letter") in LETTERS:
                        continue
                except Exception:
                    pass
            result = answer(client, q, case["text"], graph_rows[q["qid"]])
            gold = LETTERS[q["gold_index"]]
            result.update({"version": VERSION, "model": args.model, "thinking": "disabled", "external_api": False, "mask": "unmasked", "novel": novel, "batch": "first10" if novel in FIRST10 else "second10", "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": gold, "correct": result["selected_letter"] == gold})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"[{args.model}/{novel}/q{qi}] {result['selected_letter']} gold={gold}", flush=True)


if __name__ == "__main__":
    main()
