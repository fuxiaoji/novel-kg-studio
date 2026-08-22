"""C18: local evidence arbitration only when tail, C15 graph, and C12 all disagree."""

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
VERSION = "c18-local-three-way-disagreement-evidence-arbiter-v1"


def load_answers(root: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in root.rglob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("qid"):
                rows[str(row["qid"])] = row
        except Exception:
            continue
    return rows


def options_text(q: dict[str, Any]) -> str:
    return "\n".join(f"{LETTERS[index]}. {choice}" for index, choice in enumerate(q["choices"][:4]))


def graph_evidence(row: dict[str, Any], max_chars: int = 18_000) -> str:
    chunks = row.get("retrieval", {}).get("chunks", [])
    rendered = []
    used = 0
    for chunk in chunks:
        text = str(chunk.get("text", ""))
        block = f"[{chunk.get('id','')} | retrieved for {','.join(chunk.get('for_options', []))}]\n{text}"
        if used + len(block) > max_chars:
            break
        rendered.append(block)
        used += len(block)
    return "\n\n".join(rendered)


def arbitrate(
    client: NativeOllamaNoThinkClient,
    q: dict[str, Any],
    novel_text: str,
    tail: dict[str, Any],
    graph: dict[str, Any],
    c12: dict[str, Any],
) -> dict[str, Any]:
    tail_letter = normalize_letter(tail.get("selected_letter"))
    graph_letter = normalize_letter(graph.get("selected_letter"))
    c12_letter = normalize_letter(c12.get("selected_letter"))
    prompt = (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options_text(q)}\n\n"
        f"THREE INDEPENDENT READERS DISAGREE\nTail reader: {tail_letter}\nCandidate-aware graph reader: {graph_letter}\n"
        f"Older multi-retrieval reader: {c12_letter}\n\nCANDIDATE-AWARE ORIGINAL PASSAGES\n{graph_evidence(graph)}\n\n"
        f"FINAL NOVEL WINDOW\n{novel_text[-12_000:]}\n\n"
        "Do not vote and do not trust a reader merely because it is named graph or multi-retrieval. Compare each proposed "
        "answer with explicit original text. Prefer the final revelation over speculation, accusation, disguise, and red herring. "
        "For identity questions distinguish real identity, alias, victim, and killer. Missing evidence is unknown. Obey NOT/EXCEPT. "
        "Return JSON only: "
        '{"selected_letter":"A|B|C|D","evidence_source":"passage|tail|both|insufficient","decisive_evidence":"brief"}'
    )
    raw = client.complete_json("You arbitrate three disagreeing local readers using only supplied novel evidence. No chain of thought.", prompt, max_tokens=180)
    selected = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw) or c12_letter
    return {"selected_letter": selected, "raw": raw}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--out", type=Path, default=BASE / "dqa_local_c18_arbiter20")
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    tail = load_answers(BASE / "dqa_qwen35_c15_20" / "answers" / "tail")
    graph = load_answers(BASE / "dqa_qwen35_c15_20" / "answers" / "graph")
    c12 = load_answers(BASE / "dqa_qwen_c12_consensus20" / "answers")
    client = NativeOllamaNoThinkClient(args.model)
    outputs = []
    for novel in args.novels:
        case = cases[novel]
        for qi, q in enumerate(case["questions"]):
            qid = q["qid"]
            votes = [normalize_letter(tail[qid]["selected_letter"]), normalize_letter(graph[qid]["selected_letter"]), normalize_letter(c12[qid]["selected_letter"])]
            three_way = len(set(votes)) == 3
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            if path.exists():
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                    if old.get("version") == VERSION and old.get("selected_letter") in LETTERS:
                        outputs.append(old)
                        continue
                except Exception:
                    pass
            if three_way:
                result = arbitrate(client, q, case["text"], tail[qid], graph[qid], c12[qid])
                route = "local_evidence_arbiter"
            else:
                result = {"selected_letter": votes[0] if votes[0] == votes[1] else votes[0] if votes[0] == votes[2] else votes[1], "raw": None}
                route = "majority"
            gold = LETTERS[q["gold_index"]]
            result.update({"version": VERSION, "model": args.model, "thinking": "disabled", "external_api": False, "mask": "unmasked", "novel": novel, "batch": "first10" if novel in FIRST10 else "second10", "qi": qi, "qid": qid, "question": q["question"], "choices": q["choices"], "gold_letter": gold, "correct": result["selected_letter"] == gold, "route": route, "votes": {"tail35": votes[0], "graph35": votes[1], "c12": votes[2]}})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            outputs.append(result)
            if three_way:
                print(f"[arbiter/{novel}/q{qi}] {votes}->{result['selected_letter']} gold={gold}", flush=True)
    for batch in ("first10", "second10"):
        rows = [row for row in outputs if row["batch"] == batch]
        print(batch, sum(row["correct"] for row in rows), "/", len(rows), "arbitrated", sum(row["route"] == "local_evidence_arbiter" for row in rows), flush=True)


if __name__ == "__main__":
    main()
