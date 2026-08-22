"""Gold-coverage analysis: per-order coverage, gold evidence positions, gold node ids."""

from __future__ import annotations

import json
import re
from pathlib import Path

from novel_kg_studio.cache import save_json
from novel_kg_studio.schema import norm_text
from novel_kg_studio.store.bm25 import tokenize

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"
ANNO = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "anno_103.json"
NOVEL = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "novel_103.txt"
STOP = {"the", "a", "an", "of", "to", "in", "on", "at", "was", "were", "is", "are", "and", "or", "did", "do", "what", "who", "how", "he", "she", "his", "her", "it", "they"}


def _tokens(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in STOP and len(t) > 1}


def _paragraph_position(novel_text: str, number: int) -> float | None:
    match = re.search(rf"(?m)^\[{number}\]\s", novel_text)
    return match.start() / max(len(novel_text), 1) if match else None


def main() -> None:
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    rows = json.loads((OUT / "question_set_results.json").read_text(encoding="utf-8"))
    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    anno = json.loads(ANNO.read_text(encoding="utf-8"))[0]["questions"][0]
    novel_text = NOVEL.read_text(encoding="utf-8")
    reasoning = anno.get("reasoning") or []
    answer = str(anno.get("answer") or "")
    gold_tokens = _tokens(" ".join(reasoning) + " " + answer)

    by_name: dict[str, str] = {}
    by_id = {n["id"]: n for n in graph["nodes"]}
    for node in graph["nodes"]:
        by_name.setdefault(norm_text(node["name"]), node["id"])

    def coverage(names: list[str]) -> float:
        covered: set[str] = set()
        for name in names:
            node_id = by_name.get(norm_text(name))
            node = by_id.get(node_id) if node_id else None
            if node is None:
                continue
            text = " ".join([node["name"], *node.get("aliases", []), *node.get("evidence", [])])
            covered |= _tokens(text)
        return len(covered & gold_tokens) / max(len(gold_tokens), 1)

    per_question = []
    for row in rows:
        first = row["first_order"]
        second = row["second_order"]
        third = row["third_order"]
        per_question.append(
            {
                "question": row["question"],
                "mask": row["mask"],
                "coverage_first": round(coverage(first), 3),
                "coverage_first_second": round(coverage(first + second), 3),
                "coverage_first_second_third": round(coverage(first + second + third), 3),
                "counts": {"first": len(first), "second": len(second), "third": len(third)},
            }
        )

    gold_positions: list[dict] = []
    answer_position = int(anno.get("answer_position") or -1)
    if answer_position >= 0:
        gold_positions.append(
            {
                "kind": "answer_paragraph",
                "paragraph": answer_position,
                "text_position": _paragraph_position(novel_text, answer_position),
                "snippet": "答案段（凶手离开方式）",
            }
        )
    for clue in [int(p) for p in (anno.get("clue_position") or []) if isinstance(p, (int, float)) and p >= 0]:
        gold_positions.append(
            {
                "kind": "clue_paragraph",
                "paragraph": clue,
                "text_position": _paragraph_position(novel_text, clue),
                "snippet": "官方线索段",
            }
        )
    for idx, sentence in enumerate(reasoning):
        content = [t for t in tokenize(sentence) if t not in STOP and len(t) >= 4]
        seq = None
        for token in sorted(content, key=len, reverse=True):
            for row in kept:
                if token in norm_text(row["text"]):
                    seq = row["seq"]
                    break
            if seq is not None:
                break
        gold_positions.append(
            {
                "kind": "reasoning_step",
                "step": idx + 1,
                "text_position": float(kept[seq]["text_position"]) if seq is not None else None,
                "snippet": sentence[:80],
            }
        )

    gold_node_ids: list[str] = []
    scored: list[tuple[int, str]] = []
    for node in graph["nodes"]:
        text = " ".join([node["name"], *node.get("aliases", []), *node.get("evidence", [])])
        overlap = len(_tokens(text) & gold_tokens)
        if overlap > 0:
            scored.append((overlap, node["id"]))
    scored.sort(key=lambda item: -item[0])
    gold_node_ids = [node_id for _, node_id in scored[:40]]

    payload = {
        "gold_tokens": sorted(gold_tokens),
        "gold_reasoning_steps": len(reasoning),
        "gold_answer": answer,
        "gold_evidence_positions": gold_positions,
        "per_question": per_question,
        "gold_node_ids": gold_node_ids,
    }
    save_json(OUT / "gold_analysis.json", payload)

    avg_first = sum(r["coverage_first"] for r in per_question) / len(per_question)
    avg_first_second = sum(r["coverage_first_second"] for r in per_question) / len(per_question)
    avg_first_second_third = sum(r["coverage_first_second_third"] for r in per_question) / len(per_question)
    print("== 每道题金标覆盖（官方推理词命中率）==")
    for r in per_question:
        print(
            f"  mask={r['mask']:.2f} 一阶={r['coverage_first']:.3f} 一阶+二阶={r['coverage_first_second']:.3f} "
            f"+三阶={r['coverage_first_second_third']:.3f} | {r['question'][:42]}"
        )
    print(f"\n平均: 一阶={avg_first:.3f} 一阶+二阶={avg_first_second:.3f} +三阶={avg_first_second_third:.3f}")
    print("\n== 官方推理句在文本尺上的位置 ==")
    visible = {p["text_position"] for p in gold_positions if p["text_position"] is not None}
    for p in gold_positions:
        pos = p["text_position"]
        label = p.get("step", p.get("paragraph", p.get("kind")))
        print(f"  {p['kind']} {label}: 文本位置 {pos:.3f}" if pos is not None else f"  {p['kind']} {label}: 未定位")
    if visible:
        print(f"  范围: {min(visible):.3f} ~ {max(visible):.3f}")
    print("\n== 掩码挡住了多少官方推理句 ==")
    for r in per_question:
        if r["mask"] < 0.99:
            blocked = sum(1 for p in gold_positions if (p["text_position"] or 0) > r["mask"])
            print(f"  mask={r['mask']:.2f}: 挡住 {blocked}/{len(gold_positions)} 条官方推理句")
    print(f"\ngold_node_ids: {len(gold_node_ids)} 个（用于 3D 高亮）")


if __name__ == "__main__":
    main()
