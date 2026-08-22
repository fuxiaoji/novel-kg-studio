"""Dense (embedding) retrieval recall measurement on kept spans, vs gold evidence."""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"
DATA = ROOT.parent / "datasets" / "external" / "detectiveqa"
SENT_RE = re.compile(r"[^.!?\n]+[.!?]*")
BATCH = 256


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_RE.findall(text) if len(s.strip()) > 12]


def para_map(novel_id: str) -> dict[int, str]:
    f = list((DATA / "novel_data_en").glob(f"{novel_id}-*.txt"))[0]
    text = f.read_text(encoding="utf-8")
    pat = re.compile(r"(?ms)^\[(\d+)\]\s*(.*?)(?=^\[\d+\]\s*|\Z)")
    return {int(m.group(1)): m.group(2).strip() for m in pat.finditer(text)}


def load_kept(novel_id: str) -> list[dict]:
    p = OUT / "novels" / novel_id / "pass1" / "kept.jsonl"
    if p.exists():
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = []
    for rf in sorted((OUT / "novels" / novel_id / "pass1").glob("s*/chunk_*.json")):
        rec = json.loads(rf.read_text(encoding="utf-8"))
        for k in rec.get("kept") or []:
            rows.append({"text": k.get("text", ""), "char_start": k.get("char_start", 0)})
    rows.sort(key=lambda r: r["char_start"])
    seen = set()
    out = []
    for r in rows:
        key = normalize(r["text"])
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def embed(texts: list[str], model: str = "nomic-embed-text") -> np.ndarray:
    if not texts:
        return np.zeros((0, 768), dtype="float32")
    parts = []
    for i in range(0, len(texts), BATCH):
        body = {"model": model, "input": texts[i : i + BATCH]}
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/embed",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as r:
            parts.append(np.array(json.loads(r.read().decode())["embeddings"], dtype="float32"))
    return np.vstack(parts)


def main() -> None:
    total_gold = total_hit = 0
    for nid in ["103", "104", "117"]:
        kept = load_kept(nid)
        texts = [r["text"] for r in kept]
        cache = OUT / f"emb_{nid}.npy"
        if cache.exists():
            E = np.load(cache)
        else:
            E = embed(texts)
            np.save(cache, E)
        E = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
        paras = para_map(nid)
        for src in ["AIsup_anno", "human_anno"]:
            ap = DATA / "anno_data_en" / src / f"{nid}.json"
            if not ap.exists():
                continue
            anno = json.loads(ap.read_text(encoding="utf-8"))[0]
            for qi, q in enumerate(anno["questions"]):
                question = str(q["question"]).strip()
                gold = [s for cp in q.get("clue_position") or [] if cp >= 0 and cp in paras for s in sentences(paras[cp])]
                qv = embed([question])[0]
                qv = qv / (np.linalg.norm(qv) + 1e-9)
                sims = E @ qv
                top = np.argsort(sims)[::-1][:40]
                pool = [normalize(texts[i]) for i in top]
                hit = sum(1 for s in gold if any(normalize(s) in c or c in normalize(s) for c in pool))
                total_gold += len(gold)
                total_hit += hit
                print(f"{nid} Q{qi}: dense40 {hit}/{len(gold)}", flush=True)
    print(f"TOTAL dense top-40: {total_hit}/{total_gold} = {total_hit / total_gold * 100:.1f}%")


if __name__ == "__main__":
    main()
