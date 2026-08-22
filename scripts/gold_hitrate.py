"""Compute gold-evidence hit rates for the Qwen graph pipeline on DetectiveQA.

For each question: gold evidence = novel paragraphs at clue_position.
Metrics: gold sentences found in (a) pass1 kept spans, (b) graph node evidence,
(c) top-k retrieval sentences; plus the answer paragraph hit rate.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_four_datasets import load_cases  # noqa: E402
from novel_kg_studio.store.bm25 import BM25Index  # noqa: E402
from novel_kg_studio.store.llm_retriever import execute, plan_search, top_sentences  # noqa: E402
from novel_kg_studio.store import GraphStore  # noqa: E402

OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"
NOVELS = ["103", "104", "117"]
DATA = ROOT.parent / "datasets" / "external" / "detectiveqa"
SENT_RE = re.compile(r"[^.!?\n]+[.!?]*")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_RE.findall(text) if len(s.strip()) > 12]


def paragraph_map(novel_id: str) -> dict[int, str]:
    files = list((DATA / "novel_data_en").glob(f"{novel_id}-*.txt"))
    if not files:
        return {}
    text = files[0].read_text(encoding="utf-8")
    pat = re.compile(r"(?ms)^\[(\d+)\]\s*(.*?)(?=^\[\d+\]\s*|\Z)")
    return {int(m.group(1)): m.group(2).strip() for m in pat.finditer(text)}


def load_anno(novel_id: str) -> list[dict]:
    rows = []
    for source in ["AIsup_anno", "human_anno"]:
        p = DATA / "anno_data_en" / source / f"{novel_id}.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        anno = data[0] if isinstance(data, list) else data
        for qi, q in enumerate(anno.get("questions") or []):
            rows.append({"novel": novel_id, "source": source, "qi": qi, "question": str(q.get("question") or "").strip(), "clue_position": q.get("clue_position") or [], "answer_position": int(q.get("answer_position") or -1)})
    return rows


def kept_texts(novel_id: str) -> list[dict]:
    p = OUT / "novels" / novel_id / "pass1" / "kept.jsonl"
    if not p.exists():
        # reconstruct from pass1 chunk caches
        rows = []
        cache_dir = OUT / "novels" / novel_id / "pass1"
        for rec_file in sorted(cache_dir.glob("s*/chunk_*.json")):
            rec = json.loads(rec_file.read_text(encoding="utf-8"))
            for k in rec.get("kept") or []:
                rows.append({"seq": len(rows), "text": k.get("text", ""), "char_start": k.get("char_start", 0)})
        rows.sort(key=lambda r: r["char_start"])
        seen = set()
        out = []
        for r in rows:
            key = normalize(r["text"])
            if key and key not in seen:
                seen.add(key)
                out.append(r)
        return out
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def graph_evidence(novel_id: str) -> list[str]:
    p = OUT / "novels" / novel_id / "graph.json"
    if not p.exists():
        return []
    g = json.loads(p.read_text(encoding="utf-8"))
    return [e for n in g["nodes"] for e in n.get("evidence", [])]


def hit_ratio(gold_sents: list[str], corpus: list[str]) -> tuple[int, int]:
    norm_corpus = [normalize(t) for t in corpus]
    hit = 0
    for s in gold_sents:
        ns = normalize(s)
        if any(ns in c or c in ns for c in norm_corpus):
            hit += 1
    return hit, len(gold_sents)


def main() -> None:
    client = None
    try:
        from eval_four_datasets import OllamaClient

        client = OllamaClient("qwen2.5:7b-c4", num_ctx=4096)
    except Exception:
        pass
    all_rows = []
    for nid in NOVELS:
        paras = paragraph_map(nid)
        kept = kept_texts(nid)
        graph = graph_evidence(nid)
        store = None
        graph_path = OUT / "novels" / nid / "graph.json"
        if graph_path.exists():
            g = json.loads(graph_path.read_text(encoding="utf-8"))
            store = GraphStore(g["nodes"], g["edges"])
        sentence_index = BM25Index([r["text"] for r in kept]) if kept else None
        for anno in load_anno(nid):
            gold_paras = [paras[p] for p in anno["clue_position"] if p >= 0 and p in paras]
            gold_sents = [s for t in gold_paras for s in sentences(t)]
            ans_para = paras.get(anno["answer_position"], "")
            ans_sents = sentences(ans_para)
            kept_hit, kept_tot = hit_ratio(gold_sents, [r["text"] for r in kept])
            graph_hit, graph_tot = hit_ratio(gold_sents, graph)
            retr_hit8 = retr_tot = 0
            ans_retr8 = ans_retr_tot = 0
            if store is not None and sentence_index is not None:
                try:
                    q = {"question": anno["question"], "choices": None}
                    plan = plan_search(client, anno["question"], store, cache_dir=OUT / "novels" / nid / "plans")
                    _, _ = execute(store, plan)
                    hits = top_sentences(sentence_index, anno["question"], plan, k=20)
                    retr_texts = [kept[i]["text"] for i, _, _ in hits[:8]]
                    retr_texts20 = [kept[i]["text"] for i, _, _ in hits[:20]]
                    retr_hit8, retr_tot = hit_ratio(gold_sents, retr_texts)
                    _, _ = hit_ratio(gold_sents, retr_texts20)
                    ans_retr8, ans_retr_tot = hit_ratio(ans_sents, retr_texts)
                except Exception as exc:
                    print(f"[warn] retrieval failed {nid} {anno['qi']}: {type(exc).__name__}", flush=True)
            all_rows.append(
                {
                    "novel": nid,
                    "source": anno["source"],
                    "qi": anno["qi"],
                    "question": anno["question"],
                    "num_gold_sents": len(gold_sents),
                    "kept_hit": kept_hit,
                    "graph_hit": graph_hit,
                    "retr8_hit": retr_hit8,
                    "answer_in_retr8": ans_retr8,
                    "answer_sents": ans_retr_tot,
                }
            )
            print(f"{nid} Q{anno['qi']}: gold={len(gold_sents)} kept={kept_hit}/{len(gold_sents)} graph={graph_hit}/{len(gold_sents)} retr8={retr_hit8}/{retr_tot}", flush=True)
    out = OUT / "gold_hitrate.json"
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=1), encoding="utf-8")
    # aggregate
    agg = {}
    for r in all_rows:
        k = r["novel"]
        a = agg.setdefault(k, {"kept": [0, 0], "graph": [0, 0], "retr8": [0, 0], "ans": [0, 0]})
        a["kept"][0] += r["kept_hit"]; a["kept"][1] += r["num_gold_sents"]
        a["graph"][0] += r["graph_hit"]; a["graph"][1] += r["num_gold_sents"]
        a["retr8"][0] += r["retr8_hit"]; a["retr8"][1] += r["num_gold_sents"]
        a["ans"][0] += r["answer_in_retr8"]; a["ans"][1] += r["answer_sents"]
    print("\n=== gold evidence hit rate ===")
    for nid, a in agg.items():
        print(f"novel {nid}: kept {a['kept'][0]}/{a['kept'][1]} | graph {a['graph'][0]}/{a['graph'][1]} | retr8 {a['retr8'][0]}/{a['retr8'][1]} | answer-in-retr8 {a['ans'][0]}/{a['ans'][1]}")
    print("saved:", out)


if __name__ == "__main__":
    main()
