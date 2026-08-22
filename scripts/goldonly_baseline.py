"""Gold-only baseline: answer using ONLY the gold evidence paragraphs (perfect retrieval ceiling)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_four_datasets import OllamaClient, UrllibClient, load_cases, options_block  # noqa: E402

OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"
DATA = ROOT.parent / "datasets" / "external" / "detectiveqa"
NOVELS = ["103", "104", "117"]


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
            rows.append(
                {
                    "novel": novel_id,
                    "source": source,
                    "qi": qi,
                    "question": str(q.get("question") or "").strip(),
                    "options": q.get("options") or {},
                    "gold_text": str((q.get("options") or {}).get(str(q.get("answer") or ""), "")).strip(),
                    "clue_position": q.get("clue_position") or [],
                    "answer_position": int(q.get("answer_position") or -1),
                }
            )
    return rows


def answer(client, system: str, prompt: str, path: Path) -> str:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["answer"]
    raw = ""
    for _ in range(4):
        try:
            raw = client.complete(system, prompt, max_tokens=1500)
            if raw.strip():
                break
        except Exception:
            pass
    if raw.strip():
        path.write_text(json.dumps({"answer": raw}, ensure_ascii=False), encoding="utf-8")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="qwen", choices=["qwen", "deepseek"])
    parser.add_argument("--variants", nargs="+", default=["masked", "unmasked"])
    parser.add_argument("--novels", nargs="+", default=["103", "104", "117"])
    parser.add_argument("--model", default="qwen2.5:7b", help="local Ollama model for answering")
    parser.add_argument("--out-root", default=str(OUT), help="output root directory (goldonly/ lives under it)")
    parser.add_argument("--out", default=str(OUT / "results_goldonly.json"))
    args = parser.parse_args()
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    if args.backend == "qwen":
        client = OllamaClient(args.model, num_ctx=32768 if "32k" in args.model else 16384)
    else:
        client = UrllibClient("deepseek-v4-flash", reasoning_effort="low")
    rows = []
    for nid in args.novels:
        paras = paragraph_map(nid)
        out_dir = out_root / "goldonly" / nid
        out_dir.mkdir(parents=True, exist_ok=True)
        for anno in load_anno(nid):
            gold_paras = [(p, paras[p]) for p in anno["clue_position"] if p >= 0 and p in paras]
            if not gold_paras:
                continue
            opt = "Options:\n" + "\n".join(f"{k}. {v}" for k, v in sorted(anno["options"].items()))
            row = {
                "novel": nid,
                "source": anno["source"],
                "qi": anno["qi"],
                "question": anno["question"],
                "gold_text": anno["gold_text"],
            }
            for variant in args.variants:
                if variant == "masked":
                    paras_used = [(p, t) for p, t in gold_paras if p < anno["answer_position"]]
                else:
                    paras_used = gold_paras
                if not paras_used:
                    continue
                key = hashlib.sha1(f"{nid}|{anno['source']}|{anno['qi']}|{variant}|v1|{args.model}".encode("utf-8")).hexdigest()[:10]
                evidence = "\n\n".join(t for _, t in paras_used)
                prompt = (
                    f"Question: {anno['question']}\n\n{opt}\n\nEvidence:\n{evidence}\n\n"
                    "Answer with the option letter and its text."
                )
                ans = answer(client, "You are a careful detective-novel reader using ONLY the provided evidence.", prompt, out_dir / f"{variant}_{key}.json")
                row[variant] = {"answer": ans}
            if any(v in row for v in args.variants):
                rows.append(row)
    out = Path(args.out)
    out.write_text(json.dumps({"backend": args.backend, "groups": args.variants, "results": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
