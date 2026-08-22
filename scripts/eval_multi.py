"""Three-novel evaluation on official questions (with options): graph RAG vs full-text."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.store import GraphStore
from novel_kg_studio.store.bm25 import BM25Index
from novel_kg_studio.store.llm_retriever import execute, plan_search, top_sentences
from novel_kg_studio.store.suspects import format_suspect_chain, is_who_question, suspect_chain

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def official_questions(anno_path: Path, limit: int = 4) -> list[dict]:
    data = json.loads(anno_path.read_text(encoding="utf-8"))
    questions = data[0]["questions"] if isinstance(data, list) else data.get("questions") or []
    rows = []
    for q in questions[:limit]:
        options = [str(v) for v in (q.get("options") or {}).values()]
        answer_letter = str(q.get("answer") or "").strip().upper()
        answer_text = ""
        if answer_letter and options and "A" <= answer_letter <= chr(ord("A") + len(options) - 1):
            answer_text = options[ord(answer_letter) - ord("A")]
        rows.append(
            {
                "question": str(q.get("question") or "").strip(),
                "choices": options,
                "gold": answer_text,
                "gold_letter": answer_letter,
            }
        )
    return rows


def answer(client, key: str, prompt: str, cache_dir: Path, *, max_tokens: int = 3000) -> str:
    path = cache_dir / f"{key}.json"
    cached = load_json(path)
    if cached:
        return cached["answer"]
    raw = ""
    error = ""
    for _ in range(3):
        try:
            raw = client.complete(
                "You are a careful detective-novel reader answering from the provided clues only.",
                prompt,
                max_tokens=max_tokens,
            )
            if raw.strip():
                break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    save_json(path, {"answer": raw, "error": error})
    return raw


def judge(client, key: str, question: str, gold: str, model_answer: str, cache_dir: Path) -> tuple[bool, str]:
    path = cache_dir / f"j_{key}.json"
    cached = load_json(path)
    if cached:
        return bool(cached["correct"]), str(cached["note"])
    payload = client.complete_json(
        "You are a strict but fair answer judge for a detective-novel QA benchmark.",
        (
            f"Question: {question}\nGold answer: {gold}\nModel answer: {model_answer}\n"
            'Return strict JSON only: {"correct": true/false, "note": "one short reason"}'
        ),
    )
    verdict = {
        "correct": bool(payload.get("correct")) if isinstance(payload, dict) else False,
        "note": str(payload.get("note") or "") if isinstance(payload, dict) else "",
    }
    save_json(path, verdict)
    return verdict["correct"], verdict["note"]


def run_novel(client, novel_id: str, cfg: dict) -> dict:
    out_dir = _resolve(ROOT, cfg["output_dir"])
    graph = json.loads((out_dir / "graph.json").read_text(encoding="utf-8"))
    kept = [
        json.loads(line)
        for line in (out_dir / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    store = GraphStore(graph["nodes"], graph["edges"])
    sentence_index = BM25Index([row["text"] for row in kept])
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in graph["nodes"])).encode("utf-8")).hexdigest()[:8]
    full_text = "\n".join(row["text"] for row in sorted(kept, key=lambda r: r["seq"]))
    questions = official_questions(_resolve(ROOT, cfg["anno_path"]))
    cache_dir = out_dir / "multi_eval"
    rows = []
    for q in questions:
        opt_block = "Options:\n" + "\n".join(f"{chr(65 + i)}. {c.strip()}" for i, c in enumerate(q["choices"]))
        plan = plan_search(client, q["question"], store, cache_dir=out_dir / "plans", graph_fp=graph_fp)
        first, second = execute(store, plan)
        sent_hits = top_sentences(sentence_index, q["question"], plan, k=6)
        sent_texts = [kept[i]["text"] for i, _, _ in sent_hits]
        clue_lines = []
        for node_id in first[:6]:
            node = store.by_id[node_id]
            clue_lines.append(f"- {node['name']} [{node['type']}]: {node.get('description','')} {' | '.join(node.get('evidence', [])[:2])}".strip())
        suspect_block = ""
        if is_who_question(q["question"]):
            victim_match = re.search(r"(?:killed|murder(?:er)? of|thief of|stole)\s+([A-Z][\w.' ]+?)(?:\s+is|\s+was|\s+\(|\?|$)", q["question"])
            victim_name = victim_match.group(1).strip() if victim_match else ""
            suspects = suspect_chain(store, q["question"], victim_name=victim_name)
            for suspect in suspects:
                node_id = next((n["id"] for n in store.nodes if n["name"] == suspect["name"]), None)
                if node_id is not None:
                    node = store.by_id[node_id]
                    source_sents = [kept[int(seq)]["text"] for seq in node.get("source_sentence_ids", [])[:2] if int(seq) < len(kept)]
                    if source_sents:
                        suspect["source_sentences"] = source_sents
            suspect_block = format_suspect_chain(suspects)
        prompt_graph = (
            f"Question: {q['question']}\n\n{opt_block}\n\nGraph clues:\n" + "\n".join(clue_lines)
            + "\n\nEvidence sentences:\n" + "\n".join(f"- {t}" for t in sent_texts[:6])
            + (f"\n\n{suspect_block}" if suspect_block else "")
            + "\n\nTreat illusion/decoy clues (e.g. a half-open door) as misdirection. "
            "If a suspect's guilt is refuted by evidence (e.g. a false confession protecting someone), do not choose them. "
            "Answer with the option letter and text."
        )
        prompt_full = (
            f"Question: {q['question']}\n\n{opt_block}\n\nNovel text:\n{full_text}\n\n"
            "Answer with the option letter and text."
        )
        mode_tag = (
            f"{getattr(client, 'thinking', '') or ''}|{getattr(client, 'reasoning_effort', '') or ''}"
        )
        key = hashlib.sha1(
            f"{novel_id}|{client.model}|{mode_tag}|{q['question']}".encode("utf-8")
        ).hexdigest()[:12]
        ans_graph = answer(client, f"graph_v3_{key}", prompt_graph, cache_dir)
        ans_full = answer(client, f"full_{key}", prompt_full, cache_dir)
        correct_g, note_g = judge(client, f"graph_v3_{key}", q["question"], q["gold"], ans_graph, cache_dir)
        correct_f, note_f = judge(client, f"full_v3_{key}", q["question"], q["gold"], ans_full, cache_dir)
        gl = re.search(r"\b([A-D])\b", ans_graph)
        fl = re.search(r"\b([A-D])\b", ans_full)
        gl = gl.group(1) if gl else ""
        fl = fl.group(1) if fl else ""
        if gl and fl and gl != fl:
            resolver_prompt = (
                f"Question: {q['question']}\n\n{opt_block}\n\n"
                f"Reader A (knowledge-graph evidence) chose {gl}: {ans_graph}\n"
                f"Reader B (full novel) chose {fl}: {ans_full}\n\n"
                f"Graph clues:\n" + "\n".join(clue_lines)
                + f"\n\nEvidence sentences:\n" + "\n".join(f"- {t}" for t in sent_texts[:6])
                + (f"\n\n{suspect_block}" if suspect_block else "")
                + "\n\nDecide which choice is more likely correct given the evidence. "
                'Return strict JSON only: {"answer": "letter"}'
            )
            resolved = answer(client, f"resolve_v1_{key}", resolver_prompt, cache_dir)
            resolved_letter = re.search(r"\b([A-D])\b", resolved)
            if resolved_letter and resolved_letter.group(1) in {gl, fl}:
                ensemble = ans_graph if resolved_letter.group(1) == gl else ans_full
            else:
                ensemble = ans_graph
            used_fallback = resolved_letter is not None and resolved_letter.group(1) == fl
        elif gl:
            ensemble = ans_graph
            used_fallback = False
        else:
            ensemble = ans_full
            used_fallback = True
        correct_ensemble, note_ensemble = judge(client, f"ensemble_v1_{key}", q["question"], q["gold"], ensemble, cache_dir)
        rows.append(
            {
                "novel": novel_id,
                "question": q["question"],
                "gold": q["gold"],
                "gold_letter": q["gold_letter"],
                "graph": {"answer": ans_graph, "correct": correct_g, "note": note_g},
                "full_text": {"answer": ans_full, "correct": correct_f, "note": note_f},
                "ensemble": {"answer": ensemble, "correct": correct_ensemble, "note": note_ensemble, "used_fallback": used_fallback},
            }
        )
        print(
            f"[{novel_id}] {q['question'][:38]} | graph={'对' if correct_g else '错'} full={'对' if correct_f else '错'} "
            f"ensemble={'对' if correct_ensemble else '错'}{'(回退)' if used_fallback else ''} | {ans_graph[:42]} / {ans_full[:38]}"
        )
    return {"novel": novel_id, "rows": rows, "num_questions": len(rows)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=["103", "104", "117"])
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--reasoning_effort", default=None)
    parser.add_argument("--thinking", default=None)
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    client = LLMClient(
        model=args.model,
        temperature=0.0,
        max_tokens=3000,
        retries=3,
        reasoning_effort=args.reasoning_effort,
        thinking=args.thinking,
    )
    results = []
    for novel_id in args.novels:
        cfg = (config.get("novels") or {}).get(novel_id)
        if cfg is None:
            print(f"skip unknown novel {novel_id}")
            continue
        results.append(run_novel(client, novel_id, cfg))
    save_json(OUT / "multi_results.json", results)
    print(f"\n== 三本小说官方题正确率（LLM 裁判，{args.model}，带选项）==")
    total_g = total_f = total_ensemble = total_q = 0
    for result in results:
        g = sum(1 for r in result["rows"] if r["graph"]["correct"])
        f = sum(1 for r in result["rows"] if r["full_text"]["correct"])
        en = sum(1 for r in result["rows"] if r["ensemble"]["correct"])
        n = result["num_questions"]
        total_g += g
        total_f += f
        total_ensemble += en
        total_q += n
        print(f"  novel {result['novel']}: graph {g}/{n} | full_text {f}/{n} | ensemble {en}/{n}")
    print(f"  合计: graph {total_g}/{total_q} | full_text {total_f}/{total_q} | ensemble {total_ensemble}/{total_q}")
    print("saved:", OUT / "multi_results.json")


if __name__ == "__main__":
    main()
