"""Step-by-step graph-building demo on a novel excerpt (pass1 -> pass2 -> merge)."""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

from novel_kg_studio.chunking import TextChunk
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.pipeline.merge import build_graph
from novel_kg_studio.pipeline.pass1_filter import PASS1_SYSTEM, build_pass1_user, parse_pass1_payload
from novel_kg_studio.pipeline.pass2_graph import PASS2_SYSTEM_V2, build_pass2_user, parse_pass2_payload_v2
from novel_kg_studio.schema import KeptSpan

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
NOVEL = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "novel_103.txt"


def main() -> None:
    text = NOVEL.read_text(encoding="utf-8")
    paras: dict[int, str] = {}
    current = None
    for line in text.splitlines():
        match = re.match(r"^\[(\d+)\]\s*(.*)$", line)
        if match:
            current = int(match.group(1))
            paras[current] = match.group(2).strip()
        elif current is not None and line.strip():
            paras[current] += " " + line.strip()
    excerpt = "\n".join(f"[{n}] {paras[n]}" for n in range(526, 543))
    print("=" * 70)
    print("【输入片段】novel_103 [526]-[542]")
    print(excerpt)

    chunk = TextChunk("chunk_0", 0, len(excerpt), excerpt)
    client = LLMClient(model="deepseek-chat", temperature=0.0, max_tokens=2500, retries=3)

    print("=" * 70)
    print("【第一遍】LLM 过滤 + 时间标签")
    payload1 = client.complete_json(PASS1_SYSTEM, build_pass1_user(chunk), max_tokens=2500)
    kept, dropped, skipped = parse_pass1_payload(payload1, chunk)
    print("  kept:")
    for s in kept:
        print(f"    [{s.char_start}:{s.char_end}] ({s.time_label}) {s.text[:80]}")
    print("  dropped:")
    for d in dropped:
        print(f"    ({d.reason}) {d.text[:70]}")

    kept_sorted = sorted(kept, key=lambda s: s.char_start)
    for i, s in enumerate(kept_sorted):
        s.seq = i
        s.text_position = s.char_start / max(len(excerpt), 1)
        s.time_position = i / max(len(kept_sorted) - 1, 1)

    print("=" * 70)
    print("【第二遍】LLM v2 建图（原始返回）")
    lines = [(s.seq, s.text) for s in kept_sorted]
    payload2 = client.complete_json(PASS2_SYSTEM_V2, build_pass2_user(lines), max_tokens=4000)
    entities, relations = parse_pass2_payload_v2(payload2)
    print("  entities:")
    for e in entities:
        print(f"    {e['name']} [{e['type']}] salience={e['salience']} desc={e['description'][:50]} mentions={len(e['mentions'])}")
    print("  relations:")
    for r in relations:
        decoy = " [DECOY]" if r["decoy"] else ""
        print(f"    {r['source']} --{r['type']}{decoy}--> {r['target']} | {r['evidence'][:60]}")

    record = {
        "line_indices": [i for i, _ in lines],
        "entities": entities,
        "relations": relations,
        "error": "",
    }
    kept_by_seq = {s.seq: s for s in kept_sorted}
    nodes, edges, stats = build_graph([record], kept_by_seq, max(len(excerpt), 1))
    print("=" * 70)
    print("【合并后】grounding 校验 + 去重")
    print(f"  dropped_mentions={stats['dropped_mentions']} dropped_relations={stats['dropped_relations']} dedup={stats['deduplicated_relations']}")
    print("  nodes:")
    for n in nodes:
        print(f"    {n['name']} [{n['type']}] salience={n.get('salience')} deg={n['degree']} desc={n.get('description','')[:45]} ev={n.get('evidence', [])[:1]}")
    print("  edges:")
    for e in edges:
        decoy = " [DECOY]" if e.get("decoy") else ""
        print(f"    {e['source']} --{e['type']}{decoy}--> {e['target']} | {e['evidence'][:55]}")
    print("=" * 70)
    print("mermaid:")
    print(build_mermaid(nodes, edges))


def build_mermaid(nodes, edges) -> str:
    lines = ["graph LR"]
    for n in nodes:
        label = f"{n['name']}".replace('"', "'")
        lines.append(f'  n_{n["id"]}["{label} ({n["type"]})"]')
    for e in edges:
        label = e["type"] + ("[假象]" if e.get("decoy") else "")
        lines.append(f'  n_{e["source"]} -->|"{label}"| n_{e["target"]}')
    return "\n".join(lines)


if __name__ == "__main__":
    main()
