"""MuSR local-model evaluation: basic / v4 / v5.1 / v5.2 / v7 / voting ensemble.

All LLM calls use local ollama qwen2.5:7b (16k; MuSR stories are only ~5k chars).
Graphs are built with the same local model. Answer format follows official MuSR:
final line ``ANSWER: N`` (1-based). No masking (MuSR has no answer paragraph).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import shutil
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from eval_four_datasets import (  # noqa: E402
    OllamaClient,
    build_case_graph,
    load_cases,
    options_block,
)
from novel_kg_studio.cache import load_json, save_json  # noqa: E402
from novel_kg_studio.chunking import chunk_text, find_span  # noqa: E402
from novel_kg_studio.store import GraphStore  # noqa: E402
from novel_kg_studio.store.bm25 import BM25Index  # noqa: E402
from novel_kg_studio.store.llm_retriever import execute, plan_search, top_sentences  # noqa: E402

from detectiveqa_three_groups import norm_name  # noqa: E402

OUT = ROOT / "outputs" / "four_datasets" / "musr_local"
STORY_PLAN = {"murder_mystery": 34, "object_placements": 8, "team_allocation": 34}
PROMPT_VER = "p2"

ANSWER_FMT = (
    "End your reply with exactly a line in this form: `ANSWER: <number>`, where <number> is the "
    "1-based option number you choose (e.g. `ANSWER: 1` for option 1, `ANSWER: 2` for option 2). "
    "Do NOT write the letter N, do NOT write names or initials - only the option NUMBER."
)

COMPARE_FMT = (
    "Evaluate EVERY option explicitly (evidence for and against each) before choosing; "
    "do not settle for the most suspicious-looking character."
)


def extract_index(answer: str, n_choices: int) -> int | None:
    matches = list(re.finditer(r"(?i)\bANSWER\s*[:=]?\s*(\d+)", answer))
    if matches:
        n = int(matches[-1].group(1))
        return (n - 1) if 1 <= n <= n_choices else None
    m = re.search(r"\b([A-D])\b\s*[).:]?\s*$", answer.strip(), re.M)
    if m:
        idx = ord(m.group(1)) - ord("A")
        return idx if idx < n_choices else None
    return None


def _cached(path: Path):
    return load_json(path)


def _save(path: Path, obj: Any) -> None:
    save_json(path, obj)


def answer_basic(client: Any, q: dict, text: str, out_dir: Path, key: str) -> str:
    path = out_dir / f"basic_{key}.json"
    cached = _cached(path)
    if cached:
        return cached["answer"]
    opt = options_block(q)
    prompt = (
        f"Question: {q['question']}\n\n{opt}\n\nNarrative:\n{text}\n\n"
        f"Reason step by step using ONLY the narrative. {COMPARE_FMT} "
        f"Then {ANSWER_FMT}"
    )
    raw = ""
    for _ in range(3):
        try:
            raw = client.complete(
                "You are a careful reasoning judge solving a MuSR-style story question.", prompt, max_tokens=1500
            )
            if raw.strip():
                break
        except Exception:
            pass
    _save(path, {"answer": raw})
    return raw


def _graph_clue_lines(store: GraphStore, node_ids: list[str], limit: int = 12) -> list[str]:
    lines = []
    for nid in node_ids:
        node = store.by_id.get(nid)
        if not node:
            continue
        rels = []
        for neighbor, edge in store.adj.get(nid, [])[:6]:
            nb = store.by_id.get(neighbor, {})
            rels.append(f"{edge.get('type', '?')}->{nb.get('name', '?')}")
        rel_txt = (" | " + "; ".join(rels)) if rels else ""
        lines.append(f"- {node.get('name', '?')} [{node.get('type', '?')}]{rel_txt}")
        if len(lines) >= limit:
            break
    return lines


def answer_v4(client: Any, q: dict, store: GraphStore, kept: list[dict], novel_text: str, out_dir: Path, key: str) -> str:
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in store.nodes)).encode("utf-8")).hexdigest()[:8]
    path = out_dir / f"v4_{key}_{graph_fp}.json"
    cached = _cached(path)
    if cached:
        return cached["answer"]
    plan = plan_search(client, q["question"], store, cache_dir=out_dir / "plans", graph_fp=graph_fp)
    first, second = execute(store, plan)
    sentence_index = BM25Index([row["text"] for row in kept])
    sent_hits = top_sentences(sentence_index, q["question"], plan, k=8)
    sent_texts = [kept[i]["text"] for i, _, _ in sent_hits]
    clue_lines = []
    for nid in list(first[:8]) + list(second[:10]):
        node = store.by_id.get(nid)
        if not node:
            continue
        clue_lines.append(
            f"- {node['name']} [{node['type']}]: {node.get('description', '')} | "
            f"{(node.get('evidence') or [''])[0][:140]}"
        )
    opt = options_block(q)
    prompt = (
        f"Question: {q['question']}\n\n{opt}\n\nGraph clues (entities, locations, objects, events with evidence):\n"
        + "\n".join(clue_lines)
        + "\n\nEvidence sentences:\n" + "\n".join(f"- {t}" for t in sent_texts[:8])
        + f"\n\nReason step by step from the clues (mind decoys). {COMPARE_FMT} Then {ANSWER_FMT}"
    )
    raw = ""
    for _ in range(3):
        try:
            raw = client.complete(
                "You are a careful reasoning judge answering from the provided graph clues only.", prompt, max_tokens=1500
            )
            if raw.strip():
                break
        except Exception:
            pass
    _save(path, {"answer": raw})
    return raw


def answer_v51(client: Any, q: dict, store: GraphStore, novel_text: str, out_dir: Path, key: str) -> str:
    """Agentic graph reasoning v5.1: LLM picks lookup/expand nodes step by step."""
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in store.nodes)).encode("utf-8")).hexdigest()[:8]
    path = out_dir / f"v51_{key}_{graph_fp}.json"
    cached = _cached(path)
    if cached:
        return cached["answer"]
    plan = plan_search(client, q["question"], store, cache_dir=out_dir / "plans", graph_fp=graph_fp)
    first, second = execute(store, plan)
    name_to_id = {norm_name(n["name"]): n["id"] for n in store.nodes}
    visited = set(list(first[:5]) + list(second[:5]))
    evidence = []
    for nid in list(visited):
        node = store.by_id.get(nid, {})
        evidence.append(
            f"[{node.get('type', '?')}] {node.get('name', '?')}: {node.get('description', '')} | "
            f"{(' '.join(node.get('evidence', [])[:2]))[:180]}"
        )
    opt = options_block(q)

    def frontier() -> str:
        rows, seen = [], set()
        for nid in list(visited):
            for neighbor, edge in store.adj.get(nid, [])[:8]:
                if neighbor in visited:
                    continue
                nb = store.by_id.get(neighbor, {})
                nm = nb.get("name", "?")
                if nm in seen:
                    continue
                seen.add(nm)
                rows.append(f"- {nb.get('type', '?')} '{nm}' via [{edge.get('type', '?')}] | {edge.get('evidence', '')[:80]}")
        return "\n".join(rows[:26]) or "(no unvisited neighbors)"

    final = ""
    for step in range(5):
        prompt = (
            f"Question: {q['question']}\n\n{opt}\n\nNotes so far:\n" + "\n".join(evidence[-14:])
            + f"\n\nUnvisited graph neighbors you may inspect:\n{frontier()}\n\n"
            "You are a detective reasoning over a knowledge graph. Decide your next action. Return strict JSON only:\n"
            '{"lookup": ["exact node names to inspect (max 3)"], '
            '"expand": ["node names whose original story passage to read (max 2, only if decisive)"], '
            '"answer": null or {"index": 1-based option number, "text": "short answer"}}'
        )
        try:
            action = client.complete_json(
                "You are a careful detective reasoning step by step with graph evidence.", prompt, max_tokens=700
            )
        except Exception:
            action = {}
        if not isinstance(action, dict):
            action = {}
        ans = action.get("answer")
        if isinstance(ans, dict) and (ans.get("index") or ans.get("letter")):
            idx = ans.get("index") or (ord(str(ans.get("letter", "A")).upper()) - ord("A") + 1)
            final = f"ANSWER: {idx}"
            break
        looked = False
        for nm in (action.get("lookup") or [])[:3]:
            nid = name_to_id.get(norm_name(str(nm)))
            if nid is None or nid in visited:
                continue
            visited.add(nid)
            node = store.by_id[nid]
            ev = " ".join(node.get("evidence", [])[:2])[:180]
            evidence.append(f"[{node.get('type', '?')}] {node.get('name', '?')}: {node.get('description', '')} | {ev}")
            looked = True
        for nm in (action.get("expand") or [])[:2]:
            nid = name_to_id.get(norm_name(str(nm)))
            if nid is None:
                continue
            node = store.by_id[nid]
            para = ""
            for e in node.get("evidence", [])[:3]:
                start, _ = find_span(novel_text, e)
                if start < 0:
                    continue
                para = novel_text[max(0, start - 120) : min(len(novel_text), start + len(e) + 500)].replace("\n", " ")
                break
            if para:
                evidence.append(f"<story passage of {node.get('name', '?')}> {para}")
                looked = True
        if not looked and not (action.get("lookup") or action.get("expand")):
            break
        if len(evidence) > 30:
            break
    if not final:
        prompt = (
            f"Question: {q['question']}\n\n{opt}\n\nEvidence gathered:\n" + "\n".join(evidence[-16:])
            + f"\n\nReason briefly. {COMPARE_FMT} Then {ANSWER_FMT}"
        )
        try:
            final = client.complete(
                "You are a careful detective-novel reader answering from the provided clues only.",
                prompt,
                max_tokens=900,
            )
        except Exception:
            final = ""
    _save(path, {"answer": final})
    return final


def answer_v52(client: Any, q: dict, store: GraphStore, novel_text: str, out_dir: Path, key: str) -> str:
    """Agentic graph reasoning v5.2: lookup/search/traverse/expand along relation edges."""
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in store.nodes)).encode("utf-8")).hexdigest()[:8]
    path = out_dir / f"v52_{key}_{graph_fp}.json"
    cached = _cached(path)
    if cached:
        return cached["answer"]
    plan = plan_search(client, q["question"], store, cache_dir=out_dir / "plans", graph_fp=graph_fp)
    first, second = execute(store, plan)
    opt = options_block(q)
    name_index: dict[str, list[str]] = {}
    for node in store.nodes:
        for nm in [node.get("name", "")] + [str(a) for a in node.get("aliases", [])]:
            if nm:
                name_index.setdefault(norm_name(nm), []).append(node["id"])

    def find_nodes(query: str) -> list[str]:
        qn = norm_name(query)
        if not qn:
            return []
        if qn in name_index:
            return name_index[qn]
        return [nid for k, ids in name_index.items() if qn in k or k in qn for nid in ids][:3]

    def node_view(nid: str) -> str:
        node = store.by_id.get(nid, {})
        ev = " | ".join(node.get("evidence", [])[:2])[:200]
        rels = []
        for neighbor, edge in store.adj.get(nid, [])[:12]:
            nb = store.by_id.get(neighbor, {})
            rels.append(f"{edge.get('type', '?')}->{nb.get('name', '?')}({edge.get('evidence', '')[:50]})")
        rel_txt = ("\n   relations: " + "; ".join(rels)) if rels else ""
        return f"[{node.get('type', '?')}] {node.get('name', '?')}: {node.get('description', '')} | {ev}{rel_txt}"

    def paragraph_for(nid: str) -> str:
        node = store.by_id.get(nid, {})
        for e in node.get("evidence", [])[:4]:
            start, _ = find_span(novel_text, e)
            if start < 0:
                continue
            return novel_text[max(0, start - 120) : min(len(novel_text), start + len(e) + 500)].replace("\n", " ")
        return ""

    def frontier(visited: set[str]) -> str:
        rows, seen = [], set()
        for nid in list(visited)[-12:]:
            for neighbor, edge in store.adj.get(nid, [])[:10]:
                if neighbor in visited:
                    continue
                nb = store.by_id.get(neighbor, {})
                nm = nb.get("name", "?")
                if nm in seen:
                    continue
                seen.add(nm)
                rows.append(f"- {nb.get('type', '?')} '{nm}' via [{edge.get('type', '?')}] | {edge.get('evidence', '')[:70]}")
        return "\n".join(rows[:28]) or "(no unvisited neighbors)"

    chunks = chunk_text(novel_text, size=4000, overlap=200)
    chunk_index = BM25Index([c.text for c in chunks])
    visited = set(list(first[:5]) + list(second[:5]))
    notes = [node_view(nid) for nid in list(visited)]
    seed_names = [store.by_id.get(nid, {}).get("name", "") for nid in list(visited)[:14] if store.by_id.get(nid)]
    seeded = 0
    for c in chunks:
        if seeded >= 4:
            break
        if any(nm and norm_name(nm) in norm_name(c.text) for nm in seed_names):
            notes.append(f"[entity-anchored story chunk] {c.text[:1000]}")
            seeded += 1
    if seeded == 0:
        scores = chunk_index.score(q["question"])
        for i in scores.argsort()[::-1][:2]:
            if scores[i] > 0:
                notes.append(f"[retrieved story chunk] {chunks[i].text[:1000]}")
    final = ""
    stagnant = 0
    for step in range(6):
        prompt = (
            f"Question: {q['question']}\n\n{opt}\n\nEvidence notes so far:\n" + "\n".join(notes[-18:])
            + f"\n\nUnvisited graph neighbors (relation edges you may traverse):\n{frontier(visited)}\n\n"
            "You reason over the knowledge graph. Return strict JSON only with these actions:\n"
            '{"lookup": ["node names to inspect (max 3)"], '
            '"search": [{"query": "evidence description to search in the story (max 2)"}], '
            '"traverse": [{"from": "node name", "relation": "motive|means|opportunity|contradicts|related_to|located_at|witnessed_by|appears_at|supports|belongs_to|mentions|temporal_sequence|any"}], '
            '"expand": ["node names whose story passage to read (max 2)"], '
            '"answer": null or {"index": 1-based option number, "text": "short answer"}}'
        )
        try:
            action = client.complete_json(
                "You are a detective reasoning step by step through a knowledge graph; follow relation edges to reach evidence.",
                prompt,
                max_tokens=700,
            )
        except Exception:
            action = {}
        if not isinstance(action, dict):
            action = {}
        ans = action.get("answer")
        if isinstance(ans, dict) and (ans.get("index") or ans.get("letter")):
            idx = ans.get("index") or (ord(str(ans.get("letter", "A")).upper()) - ord("A") + 1)
            final = f"ANSWER: {idx}"
            break
        added = 0
        for nm in (action.get("lookup") or [])[:3]:
            ids = find_nodes(str(nm))
            new = [nid for nid in ids if nid not in visited]
            for nid in new:
                visited.add(nid)
                notes.append(node_view(nid))
                added += 1
        for sq in (action.get("search") or [])[:2]:
            if not isinstance(sq, dict):
                continue
            qtxt = str(sq.get("query", ""))
            if not qtxt:
                continue
            s = chunk_index.score(qtxt)
            for ci in s.argsort()[:2]:
                if s[ci] <= 0:
                    continue
                c = chunks[ci]
                if c.text[:1200] not in [n.split("] ", 1)[-1] for n in notes]:
                    notes.append(f"[retrieved story chunk] {c.text[:1200]}")
                    added += 1
        for t in (action.get("traverse") or [])[:3]:
            if not isinstance(t, dict):
                continue
            frm = find_nodes(str(t.get("from", "")))
            rel = str(t.get("relation", "any")).lower()
            for nid in frm[:2]:
                for neighbor, edge in store.adj.get(nid, [])[:20]:
                    if rel not in ("any", "all") and edge.get("type", "").lower() != rel:
                        continue
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    notes.append(node_view(neighbor))
                    added += 1
        for nm in (action.get("expand") or [])[:2]:
            for nid in find_nodes(str(nm))[:2]:
                para = paragraph_for(nid)
                if para:
                    notes.append(f"<story passage of {store.by_id[nid].get('name', '?')}> {para}")
                    added += 1
        if added == 0 and not (action.get("lookup") or action.get("search") or action.get("traverse") or action.get("expand")):
            break
        stagnant = stagnant + 1 if added == 0 else 0
        if stagnant >= 2:
            break
    if not final:
        prompt = (
            f"Question: {q['question']}\n\n{opt}\n\nEvidence gathered:\n" + "\n".join(notes[-18:])
            + f"\n\nReason briefly. {COMPARE_FMT} Then {ANSWER_FMT}"
        )
        try:
            final = client.complete(
                "You are a careful detective-novel reader answering from the provided clues only.",
                prompt,
                max_tokens=900,
            )
        except Exception:
            final = ""
    _save(path, {"answer": final})
    return final


def answer_v7(client: Any, q: dict, store: GraphStore, novel_text: str, out_dir: Path, key: str) -> str:
    """v7: option-aware evidence extraction from entity-anchored chunks, then answer."""
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in store.nodes)).encode("utf-8")).hexdigest()[:8]
    path = out_dir / f"v7_{key}_{graph_fp}.json"
    cached = _cached(path)
    if cached:
        return cached["answer"]
    plan = plan_search(client, q["question"], store, cache_dir=out_dir / "plans", graph_fp=graph_fp)
    first, second = execute(store, plan)
    chunks = chunk_text(novel_text, size=4000, overlap=200)
    node_ids = list(first[:8]) + list(second[:12])
    names = [store.by_id[n].get("name", "") for n in node_ids if n in store.by_id]
    picked = []
    for c in chunks:
        if len(picked) >= 5:
            break
        if any(nm and norm_name(nm) in norm_name(c.text) for nm in names):
            picked.append(c)
    if len(picked) < 3:
        scores = BM25Index([c.text for c in chunks]).score(
            " ".join([q["question"], plan.search_terms, plan.hypothetical_clue])
        )
        for i in scores.argsort()[::-1]:
            if len(picked) >= 5:
                break
            c = chunks[i]
            if c not in picked and scores[i] > 0:
                picked.append(c)
    opt = options_block(q)
    numbered = "\n\n".join(f"### Chunk {i}\n{c.text}" for i, c in enumerate(picked))
    clue_block = "\n" + "\n".join(_graph_clue_lines(store, node_ids))
    extr_prompt = (
        f"Question: {q['question']}\n\n{opt}{clue_block}\n\nStory excerpts:\n{numbered}\n\n"
        "For EACH option, quote up to 2 verbatim sentences from the excerpts that are evidence FOR or AGAINST "
        "that option (supporting clues, contradictions, decoys). Use the key entities/relations to focus. "
        "Return strict JSON only: " '{"evidence": {"1": ["quote", ...], "2": [...], ...}}'
    )
    quotes: dict[str, list[str]] = {}
    try:
        payload = client.complete_json(
            "You are an expert evidence extractor for story reasoning.", extr_prompt, max_tokens=1200
        )
        if isinstance(payload, dict):
            quotes = {str(k): [str(s) for s in v if isinstance(v, list)] for k, v in payload.get("evidence", {}).items()}
    except Exception:
        quotes = {}
    grounded: dict[str, list[str]] = {}
    for letter, lst in quotes.items():
        kept_q = []
        for s in lst:
            if any(find_span(c.text, s)[0] >= 0 for c in picked):
                kept_q.append(s)
        if kept_q:
            grounded[letter] = kept_q[:2]
    evidence_block = (
        "\n".join(f"- {letter}: " + " | ".join(grounded[letter]) for letter in sorted(grounded))
        if grounded
        else "(no grounded evidence extracted)"
    )
    answer_prompt = (
        f"Question: {q['question']}\n\n{opt}{clue_block}\n\nExtracted evidence by option:\n{evidence_block}\n\n"
        "Decide the correct option. Treat illusion/decoy clues as misdirection. "
        f"Reason briefly. {COMPARE_FMT} Then {ANSWER_FMT}"
    )
    raw = ""
    for _ in range(3):
        try:
            raw = client.complete(
                "You are a careful detective-novel reader.", answer_prompt, max_tokens=1200
            )
            if raw.strip():
                break
        except Exception:
            pass
    _save(path, {"answer": raw})
    return raw


def majority_vote(answers: dict[str, str], q: dict) -> tuple[str, str | None]:
    n_choices = len(q.get("choices") or [])
    votes: list[tuple[str, int | None]] = [(m, extract_index(a, n_choices)) for m, a in answers.items()]
    parsed = [(m, i) for m, i in votes if i is not None]
    if not parsed:
        return "", None
    counts: dict[int, list[str]] = {}
    for m, i in parsed:
        counts.setdefault(i, []).append(m)
    best_i, best_m = max(counts.items(), key=lambda kv: (len(kv[1]), kv[1][0] == "v7"))
    # tie -> v7
    top_n = len(best_m)
    tied = [i for i, ms in counts.items() if len(ms) == top_n]
    if len(tied) > 1 and "v7" in [m for i, ms in counts.items() for m in ms if i in tied]:
        best_i = next(i for i, ms in counts.items() if i in tied and "v7" in ms)
    return f"ANSWER: {best_i + 1}", best_i


def judge_answer(answer: str, q: dict, out_dir: Path, key: str) -> tuple[bool, str]:
    n_choices = len(q.get("choices") or [])
    gold = q.get("gold_index")
    idx = extract_index(answer, n_choices)
    if idx is not None:
        return idx == gold, f"parsed {idx + 1}"
    return False, "unparsed"


def run_question(
    client: Any,
    case: dict,
    q: dict,
    store: GraphStore,
    kept: list[dict],
    cfg: dict,
    *,
    workers: int,
) -> dict:
    case_dir = OUT / case["case_id"]
    case_dir.mkdir(parents=True, exist_ok=True)
    qi = [x["qid"] for x in case["questions"]].index(q["qid"])
    key = hashlib.sha1(f"{case['case_id']}|{qi}|local|{PROMPT_VER}".encode("utf-8")).hexdigest()[:10]
    text = case["text"]
    out: dict = {
        "qid": q["qid"],
        "case_id": case["case_id"],
        "domain": case["meta"].get("domain"),
        "question": q["question"],
        "gold_index": q.get("gold_index"),
        "gold_text": q.get("gold_text"),
        "n_choices": len(q.get("choices") or []),
    }
    answers: dict[str, str] = {}
    answers["basic"] = answer_basic(client, q, text, case_dir, key)
    if store is not None:
        answers["v4"] = answer_v4(client, q, store, kept, text, case_dir, key)
        answers["v5.1"] = answer_v51(client, q, store, text, case_dir, key)
        answers["v5.2"] = answer_v52(client, q, store, text, case_dir, key)
        answers["v7"] = answer_v7(client, q, store, text, case_dir, key)
        vote_ans, vote_idx = majority_vote({"v4": answers["v4"], "v5.2": answers["v5.2"], "v7": answers["v7"]}, q)
        answers["vote"] = vote_ans
    for method, ans in answers.items():
        correct, note = judge_answer(ans, q, case_dir, f"{method}_{key}")
        out[method] = {"answer": ans, "correct": correct, "note": note}
    return out


def pick_questions(cases: list[dict], story_plan: dict[str, int]) -> list[tuple[dict, dict]]:
    picked: list[tuple[dict, dict]] = []
    for domain, n_stories in story_plan.items():
        got = 0
        for case in cases:
            if case["meta"].get("domain") != domain:
                continue
            for q in case["questions"]:
                picked.append((case, q))
            got += 1
            if got >= n_stories:
                break
    return picked


def pick_questions_domains(cases: list[dict], story_plan: dict[str, int], domains: list[str]) -> list[tuple[dict, dict]]:
    plan = {d: story_plan[d] for d in domains if d in story_plan}
    return pick_questions(cases, plan)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-workers", type=int, default=4)
    parser.add_argument("--answer-workers", type=int, default=6)
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--skip-build", action="store_true", help="reuse existing graphs, do not build")
    parser.add_argument("--domains", nargs="+", default=None, help="e.g. murder_mystery")
    args = parser.parse_args()

    cfg = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    cfg["model"] = dict(cfg.get("model") or {})
    cfg["model"]["max_tokens_pass1"] = 2500
    cfg["model"]["max_tokens_pass2"] = 3000
    client = OllamaClient("qwen2.5:7b", num_ctx=8192)

    cases = load_cases("musr")
    picked = pick_questions_domains(cases, STORY_PLAN, args.domains) if args.domains else pick_questions(cases, STORY_PLAN)
    print(f"picked {len(picked)} questions from {len(set(c['case_id'] for c, _ in picked))} stories", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)

    # Phase 1: build graphs (local qwen)
    needed_cases = {c["case_id"]: c for c, _ in picked}
    if not args.skip_build:
        for attempt in range(3):
            todo = []
            for cid, case in needed_cases.items():
                g = load_json(OUT / cid / "graph.json")
                if g and len(g.get("nodes") or []) >= 3:
                    continue
                todo.append(case)
            if not todo:
                break
            print(f"[build] round {attempt + 1}: rebuilding {len(todo)} empty/missing graphs", flush=True)
            for case in todo:
                cdir = OUT / case["case_id"]
                for stale in ["graph.json", "pass1", "pass2", "coref_repair.json", "groups_*.json"]:
                    p = cdir / stale
                    if p.is_dir():
                        shutil.rmtree(p, ignore_errors=True)
                    elif p.exists():
                        p.unlink(missing_ok=True)
                for stale in cdir.glob("groups_*.json"):
                    stale.unlink(missing_ok=True)
            with ThreadPoolExecutor(max_workers=args.build_workers) as pool:
                futs = {
                    pool.submit(
                        build_case_graph,
                        client,
                        case,
                        OUT / case["case_id"],
                        cfg,
                        workers=args.build_workers,
                        resume=not args.no_resume,
                    ): case["case_id"]
                    for case in todo
                }
                for fut in as_completed(futs):
                    cid = futs[fut]
                    try:
                        fut.result()
                    except Exception as exc:
                        print(f"[build] {cid} FAIL {type(exc).__name__}: {exc}", flush=True)
            ok = sum(
                1
                for cid in needed_cases
                if (g := load_json(OUT / cid / "graph.json")) and len(g.get("nodes") or []) >= 3
            )
            print(f"[build] after round {attempt + 1}: {ok}/{len(needed_cases)} valid graphs", flush=True)
            if ok == len(needed_cases):
                break

    # Phase 2: answer
    results = []
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.answer_workers) as pool:
        futs = {}
        for case, q in picked:
            graph = load_json(OUT / case["case_id"] / "graph.json")
            store = GraphStore(graph["nodes"], graph["edges"]) if graph else None
            kept = []
            kept_path = OUT / case["case_id"] / "pass1" / "kept.jsonl"
            if kept_path.exists():
                kept = [json.loads(l) for l in kept_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            futs[pool.submit(run_question, client, case, q, store, kept, cfg, workers=args.answer_workers)] = (case["case_id"], q["qid"])
        for fut in as_completed(futs):
            cid, qid = futs[fut]
            try:
                row = fut.result()
                results.append(row)
            except Exception as exc:
                print(f"[answer] {cid} {qid} FAIL {type(exc).__name__}: {exc}", flush=True)
            done += 1
            if done % 10 == 0 or done == len(futs):
                save_json(OUT / "results.json", {"total": len(futs), "done": done, "results": results})
                print(f"[progress] {done}/{len(futs)} q done in {time.time()-t0:.0f}s", flush=True)

    save_json(OUT / "results.json", {"total": len(futs), "done": done, "results": results})
    by_domain: dict[str, dict[str, list[bool]]] = {}
    for r in results:
        d = by_domain.setdefault(r["domain"], {})
        for m in ["basic", "v4", "v5.1", "v5.2", "v7", "vote"]:
            if m in r:
                d.setdefault(m, []).append(bool(r[m]["correct"]))
    print("\n=== RESULTS ===", flush=True)
    for domain, mdict in by_domain.items():
        line = " | ".join(f"{m}: {sum(v)}/{len(v)} ({sum(v)/len(v):.0%})" for m, v in mdict.items())
        print(f"{domain}: {line}", flush=True)


if __name__ == "__main__":
    main()
