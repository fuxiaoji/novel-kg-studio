"""Causal gold-evidence attribution pilot for graph vs compression contexts.

This does not claim to expose internal attention tensors. It measures gold
evidence retention and the drop in correct-option log probability after
removing gold-aligned evidence from each method's own context.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT.parent / "datasets" / "external" / "detectiveqa"
BASE = ROOT / "outputs" / "four_datasets" / "dqa30_attention"
G7 = BASE / "g7_pure_graph_tight" / "answers"
COMP = BASE / "batch03_eval" / "compression"
OUT = BASE / "attention_proxy_pilot"
LETTERS = "ABCD"
SENT_RE = re.compile(r"[^.!?\n]+[.!?]*")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def paragraph_map(novel: str) -> dict[int, str]:
    files = list((DATA / "novel_data_en").glob(f"{novel}-*.txt"))
    if not files:
        return {}
    text = files[0].read_text(encoding="utf-8")
    pattern = re.compile(r"(?ms)^\[(\d+)\]\s*(.*?)(?=^\[\d+\]\s*|\Z)")
    return {int(match.group(1)): match.group(2).strip() for match in pattern.finditer(text)}


def annotation(novel: str, qid: str) -> dict:
    source = "human_anno" if "_human_anno_" in qid else "AIsup_anno"
    qi = int(qid.rsplit("_", 1)[-1])
    path = DATA / "anno_data_en" / source / f"{novel}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    record = data[0] if isinstance(data, list) else data
    return record["questions"][qi]


def sentences(text: str) -> list[str]:
    return [item.strip() for item in SENT_RE.findall(text) if len(item.strip()) >= 20]


def embed(texts: list[str]) -> np.ndarray:
    body = {"model": "bge-m3", "input": texts}
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/embed",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        matrix = np.asarray(json.loads(response.read().decode("utf-8"))["embeddings"], dtype=np.float32)
    matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-8)
    return matrix


def option_logprobs(model: str, context: str, question: str, choices: list[str], num_ctx: int) -> dict:
    options = "\n".join(f"{LETTERS[i]}. {choice}" for i, choice in enumerate(choices[:4]))
    prompt = (
        "Use only the supplied context. Choose exactly one option letter.\n\n"
        f"CONTEXT\n{context}\n\nQUESTION\n{question}\n\nOPTIONS\n{options}\n\nAnswer:"
    )
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "think": False,
        "stream": False,
        "logprobs": True,
        "top_logprobs": 20,
        "keep_alive": "30m",
        "options": {"temperature": 0.0, "num_predict": 2, "num_ctx": num_ctx},
    }
    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=900) as response:
        payload = json.loads(response.read().decode("utf-8"))
    first = (payload.get("logprobs") or [{}])[0]
    scores = {letter: -30.0 for letter in LETTERS}
    for row in first.get("top_logprobs") or []:
        token = str(row.get("token") or "").strip().upper()
        if token in scores:
            scores[token] = max(scores[token], float(row.get("logprob") or -30.0))
    values = np.asarray([scores[letter] for letter in LETTERS], dtype=np.float64)
    values -= np.max(values)
    probs = np.exp(values) / np.exp(values).sum()
    return {
        "generated": str((payload.get("message") or {}).get("content") or "").strip(),
        "logprobs": scores,
        "normalized_probs": {letter: float(probs[i]) for i, letter in enumerate(LETTERS)},
        "prompt_tokens": int(payload.get("prompt_eval_count") or 0),
    }


def graph_context(item: dict, gold_positions: list[int]):
    chunks = item["retrieval"]["chunks"]
    gold_ids = {f"[{position}]" for position in gold_positions if position >= 0}
    hit_ids = [row["id"] for row in chunks if any(marker in row["text"] for marker in gold_ids)]
    full = "\n\n".join(f"[{row['id']}]\n{row['text']}" for row in chunks)
    ablated_rows = [row for row in chunks if row["id"] not in set(hit_ids)]
    ablated = "\n\n".join(f"[{row['id']}]\n{row['text']}" for row in ablated_rows) or "[gold-overlapping graph evidence removed]"
    return full, ablated, hit_ids


def compressed_context(novel: str, gold_paragraphs: list[str], threshold: float):
    data = json.loads((COMP / novel / "compressed.json").read_text(encoding="utf-8"))
    full = str(data["text"])
    summary_sentences = sentences(full)
    gold = [text for text in gold_paragraphs if text.strip()]
    if not gold or not summary_sentences:
        return full, full, [], []
    matrix = embed(gold + summary_sentences)
    similarities = matrix[: len(gold)] @ matrix[len(gold):].T
    remove = set()
    alignments = []
    for index, row in enumerate(similarities):
        best = int(np.argmax(row))
        score = float(row[best])
        alignments.append({"gold_index": index, "summary_index": best, "similarity": score, "sentence": summary_sentences[best]})
        if score >= threshold:
            remove.add(best)
    ablated = " ".join(sentence for i, sentence in enumerate(summary_sentences) if i not in remove) or "[gold-aligned compressed evidence removed]"
    return full, ablated, sorted(remove), alignments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--alignment-threshold", type=float, default=0.45)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    paths = sorted(G7.rglob("q*.json"), key=lambda p: (int(p.parent.name), p.name))
    paths = [path for path in paths if (COMP / path.parent.name / "compressed.json").exists()][: args.limit]
    rows = []
    for index, path in enumerate(paths, 1):
        item = json.loads(path.read_text(encoding="utf-8"))
        novel = str(item["novel"])
        anno = annotation(novel, item["qid"])
        positions = sorted({int(p) for p in (anno.get("clue_position") or []) if int(p) >= 0} | ({int(anno["answer_position"])} if int(anno.get("answer_position") or -1) >= 0 else set()))
        paragraphs = paragraph_map(novel)
        gold_paragraphs = [paragraphs[p] for p in positions if p in paragraphs]
        graph_full, graph_ablated, graph_hits = graph_context(item, positions)
        comp_full, comp_ablated, comp_removed, alignments = compressed_context(novel, gold_paragraphs, args.alignment_threshold)
        conditions = {
            "graph_full": graph_full,
            "graph_ablated": graph_ablated,
            "compression_full": comp_full,
            "compression_ablated": comp_ablated,
        }
        scores = {name: option_logprobs(args.model, context, item["question"], item["choices"], args.num_ctx) for name, context in conditions.items()}
        gold = item["gold_letter"]
        graph_delta = scores["graph_full"]["logprobs"][gold] - scores["graph_ablated"]["logprobs"][gold]
        comp_delta = scores["compression_full"]["logprobs"][gold] - scores["compression_ablated"]["logprobs"][gold]
        row = {
            "novel": novel, "qi": item["qi"], "qid": item["qid"], "gold": gold,
            "gold_positions": positions, "graph_gold_chunk_ids": graph_hits,
            "graph_gold_retained": bool(graph_hits), "compression_removed_sentence_ids": comp_removed,
            "compression_gold_retained": any(a["similarity"] >= args.alignment_threshold for a in alignments),
            "compression_alignments": alignments, "scores": scores,
            "graph_gold_logprob_delta": graph_delta, "compression_gold_logprob_delta": comp_delta,
        }
        rows.append(row)
        print(f"[{index}/{len(paths)}] {novel}/q{item['qi']} graph_hit={bool(graph_hits)} d_graph={graph_delta:.3f} comp_hit={row['compression_gold_retained']} d_comp={comp_delta:.3f}", flush=True)
    summary = {
        "metadata": {"metric": "causal gold-evidence attribution proxy, not internal attention", "model": args.model, "questions": len(rows), "alignment_threshold": args.alignment_threshold},
        "graph_gold_retention": sum(r["graph_gold_retained"] for r in rows) / len(rows),
        "compression_gold_retention": sum(r["compression_gold_retained"] for r in rows) / len(rows),
        "mean_graph_gold_logprob_delta": sum(r["graph_gold_logprob_delta"] for r in rows) / len(rows),
        "mean_compression_gold_logprob_delta": sum(r["compression_gold_logprob_delta"] for r in rows) / len(rows),
        "rows": rows,
    }
    (OUT / "pilot.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
