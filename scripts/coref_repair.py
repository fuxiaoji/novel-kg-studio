"""Coreference repair pass: resolve pronouns in relation evidence and re-attach edges."""

from __future__ import annotations

import json
import re
from pathlib import Path

from novel_kg_studio.cache import save_json
from novel_kg_studio.llm import LLMClient
from novel_kg_studio.schema import norm_text

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"

PRONOUN_TOKENS = re.compile(r"\b(he|him|his|she|her|i|me|my|we|our|they|them|their)\b", re.IGNORECASE)
PRONOUN_NAMES = {"he", "him", "his", "she", "her", "i", "me", "my", "we", "us", "our", "they", "them", "their"}
GENERIC_PATTERN = re.compile(r"^the (man|woman|girl|boy|killer|murderer|victim|narrator|doctor|servant|patient|body)$")
BATCH_SIZE = 8


def _generic(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    return lowered in PRONOUN_NAMES or bool(GENERIC_PATTERN.match(lowered))


def main() -> None:
    graph = json.loads((OUT / "graph.json").read_text(encoding="utf-8"))
    nodes, edges = graph["nodes"], graph["edges"]
    by_id = {n["id"]: n for n in nodes}
    by_name = {}
    for node in nodes:
        by_name.setdefault(norm_text(node["name"]), node["id"])

    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kept_norm = [(row["seq"], norm_text(row["text"])) for row in kept]

    def locate(evidence: str) -> int | None:
        ev = norm_text(evidence)
        head = ev[:40]
        for seq, row_norm in kept_norm:
            if head and head in row_norm:
                return seq
        return None

    pronoun_edges = [
        e for e in edges if PRONOUN_TOKENS.search(e.get("evidence", "")) and (_generic(by_id[e["source"]]["name"]) or _generic(by_id[e["target"]]["name"]))
    ]
    tasks: dict[int, list[dict]] = {}
    edge_task: dict[str, int] = {}
    for edge in pronoun_edges:
        seq = locate(edge.get("evidence", ""))
        if seq is None:
            continue
        tasks.setdefault(seq, []).append(edge)
        edge_task[edge["id"]] = seq
    print(f"pronoun edges: {len(pronoun_edges)} | contextualized: {len(edge_task)} | unique sentences: {len(tasks)}")

    by_seq = {row["seq"]: row for row in kept}
    candidate_names = [
        node["name"]
        for node in sorted(
            (n for n in nodes if n["type"] == "person" and not _generic(n["name"])),
            key=lambda n: -n["degree"],
        )[:60]
    ]

    client = LLMClient(model="deepseek-chat", temperature=0.0, max_tokens=2500, retries=3)
    antecedents: dict[int, str | None] = {}
    ordered_seqs = sorted(tasks)
    for start in range(0, len(ordered_seqs), BATCH_SIZE):
        batch = ordered_seqs[start : start + BATCH_SIZE]
        blocks = []
        for seq in batch:
            window = [by_seq[s] for s in range(max(0, seq - 1), min(len(kept), seq + 2))]
            lines = "\n".join(f"[{w['seq']}] {w['text']}" for w in window)
            blocks.append(f"### id={seq}\n{lines}\n")
        candidates = "\n".join(f"- {name}" for name in candidate_names)
        prompt = (
            "You resolve pronoun antecedents in a detective novel. Candidate characters:\n"
            + candidates
            + "\n\nFor each block, the pronoun in the TARGET line (the middle line) refers to which "
            "character? Answer with the exact candidate name, or null if unclear.\n\n"
            + "\n".join(blocks)
            + "\n\nReturn ONE strict JSON object mapping each id to a name or null, "
            'e.g. {"1840": "Bella DuVain", "1843": null}. Do not add extra fields or lines.'
        )
        payload = client.complete_json(
            "You are an expert in coreference resolution for long novels.",
            prompt,
        )
        for seq in batch:
            value = payload.get(str(seq)) if isinstance(payload, dict) else None
            antecedents[seq] = str(value).strip() if value not in (None, "", "null") else None
    print(f"resolved antecedents: {sum(1 for v in antecedents.values() if v)} / {len(antecedents)}")

    moved = 0
    unmoved = 0
    examples: list[dict] = []
    modified: dict[str, dict] = {}
    for edge in pronoun_edges:
        seq = edge_task.get(edge["id"])
        if seq is None:
            unmoved += 1
            continue
        antecedent = antecedents.get(seq)
        target_id = by_name.get(norm_text(antecedent)) if antecedent else None
        if target_id is None:
            unmoved += 1
            continue
        before = f"{by_id[edge['source']]['name']} --{edge['type']}--> {by_id[edge['target']]['name']}"
        if _generic(by_id[edge["source"]]["name"]):
            edge["source"] = target_id
            moved += 1
        elif _generic(by_id[edge["target"]]["name"]):
            edge["target"] = target_id
            moved += 1
        else:
            unmoved += 1
            continue
        after = f"{by_id[edge['source']]['name']} --{edge['type']}--> {by_id[edge['target']]['name']}"
        modified[edge["id"]] = edge
        if len(examples) < 10:
            examples.append({"before": before, "after": after, "evidence": edge["evidence"][:110], "antecedent": antecedent})

    repaired_edges = [modified.get(e["id"], e) for e in edges]
    repaired = {**graph, "edges": repaired_edges}
    save_json(OUT / "graph_coref_repaired.json", repaired)
    save_json(
        OUT / "coref_repair.json",
        {
            "pronoun_edges": len(pronoun_edges),
            "contextualized_edges": len(edge_task),
            "resolved_sentences": sum(1 for v in antecedents.values() if v),
            "moved_edges": moved,
            "unmoved_edges": unmoved,
            "examples": examples,
            "antecedents": {str(k): v for k, v in antecedents.items() if v},
        },
    )
    print(f"moved_edges={moved} unmoved={unmoved}")
    for item in examples:
        print(f"  {item['before']}  ==>  {item['after']}  | {item['evidence'][:70]}  (→ {item['antecedent']})")
    print("saved:", OUT / "graph_coref_repaired.json")


if __name__ == "__main__":
    main()
