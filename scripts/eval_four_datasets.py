"""Quick evaluation of Novel KG Studio on the four paper datasets.

For each sampled case we compare:
  - graph RAG : pass1 -> pass2 -> merge -> coref -> consolidate -> LLM retrieval -> answer
  - full text : LLM reads the whole (masked) text and answers

Backends: deepseek (OpenAI-compatible API) or ollama (local model).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from novel_kg_studio.cache import load_json, save_json
from novel_kg_studio.chunking import chunk_text
from novel_kg_studio.llm import LLMClient, extract_json
from novel_kg_studio.pipeline import run_pass1, run_pass2
from novel_kg_studio.pipeline.consolidate import consolidate_person_nodes
from novel_kg_studio.pipeline.coref import repair_graph
from novel_kg_studio.pipeline.merge import build_graph
from novel_kg_studio.pipeline.quality import evaluate_graph_quality
from novel_kg_studio.schema import KeptSpan
from novel_kg_studio.store import GraphStore
from novel_kg_studio.store.bm25 import BM25Index
from novel_kg_studio.store.llm_retriever import execute, plan_search, top_sentences

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "four_datasets"
CASES = OUT / "cases"
PROMPT_VERSION = "v2_adapted"


class OllamaClient:
    """OpenAI-compatible client for a local Ollama model."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://127.0.0.1:11434/v1",
        max_tokens: int = 3000,
        num_ctx: int = 16384,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.num_ctx = num_ctx
        self.base_url = base_url.rstrip("/")

    def complete(self, system: str, user: str, *, max_tokens: int | None = None, json_mode: bool = False) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "think": False,
            "max_tokens": max_tokens or self.max_tokens,
            "options": {"num_ctx": self.num_ctx},
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data["choices"][0]["message"] or {}).get("content") or ""

    def complete_json(self, system: str, user: str, *, max_tokens: int | None = None) -> Any:
        last_err: Exception | None = None
        native_url = f"{self.base_url.removesuffix('/v1')}/api/chat"
        for attempt in range(4):
            try:
                body = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "stream": False,
                    "think": False,
                    "format": "json",
                    "options": {
                        "num_ctx": self.num_ctx,
                        "num_predict": max_tokens or self.max_tokens,
                        "temperature": 0.0,
                    },
                }
                req = urllib.request.Request(
                    native_url,
                    data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=300) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                raw = (data.get("message") or {}).get("content") or ""
                return extract_json(raw)
            except Exception as exc:
                last_err = exc
                user = f"{user}\n\nYour previous response was invalid or empty. Return strict JSON only."
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"JSON call failed: {last_err!r}")

class QwenClient:
    """Local Qwen2.5 via llama-cpp-python (split GGUF auto-detected)."""

    def __init__(self, model_path: str, *, n_ctx: int = 16384, n_threads: int = 8) -> None:
        from llama_cpp import Llama

        self.model = Path(model_path).stem
        self.llm = Llama(model_path=model_path, n_ctx=n_ctx, n_threads=n_threads, verbose=False)

    def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> str:
        out = self.llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens or 2500,
            temperature=0.0,
        )
        return out["choices"][0]["message"]["content"] or ""

    def complete_json(self, system: str, user: str, *, max_tokens: int | None = None) -> Any:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens or self.max_tokens,
            "options": {"num_ctx": self.num_ctx},
            "format": "json",
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = (data["choices"][0]["message"] or {}).get("content") or ""
        try:
            return extract_json(content)
        except ValueError:
            return {}


class UrllibClient:
    """Thread-friendly DeepSeek client via urllib (avoids httpx thread hangs seen here)."""

    def __init__(
        self,
        model: str = "deepseek-v4-flash",
        *,
        reasoning_effort: str | None = "low",
        api_key: str = "",
        base_url: str = "https://api.deepseek.com",
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = base_url

    def _call(self, system: str, user: str, max_tokens: int) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                content = (data["choices"][0]["message"] or {}).get("content") or ""
                if content.strip():
                    return content
                raise RuntimeError("empty completion")
            except Exception as exc:
                last_err = exc
                time.sleep(2.0 * (attempt + 1))
        raise RuntimeError(f"LLM call failed: {last_err!r}")

    def complete(self, system: str, user: str, *, max_tokens: int | None = None) -> str:
        return self._call(system, user, max_tokens or 2500)

    def complete_json(self, system: str, user: str, *, max_tokens: int | None = None) -> Any:
        raw = self.complete(system, user, max_tokens=max_tokens)
        try:
            return extract_json(raw)
        except ValueError:
            return {}


def make_client(backend: str, model: str, reasoning_effort: str | None, thinking: str | None) -> Any:
    if backend == "ollama":
        return OllamaClient(model)
    if backend == "qwen":
        return QwenClient(model)
    if backend == "urllib":
        return UrllibClient(model, reasoning_effort=reasoning_effort)
    return LLMClient(
        model=model,
        temperature=0.0,
        max_tokens=3000,
        retries=3,
        reasoning_effort=reasoning_effort,
        thinking=thinking,
    )


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def load_cases(dataset: str) -> list[dict]:
    path = CASES / f"{dataset}.jsonl"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def options_block(q: dict) -> str:
    choices = q.get("choices") or []
    if not choices:
        return ""
    return "Options:\n" + "\n".join(f"{chr(65 + i)}. {c.strip()}" for i, c in enumerate(choices))


def synthetic_kept(text: str) -> list[KeptSpan]:
    spans: list[KeptSpan] = []
    offset = 0
    seq = 0
    for line in text.splitlines():
        if line.strip():
            spans.append(
                KeptSpan(
                    text=line,
                    chunk_idx=0,
                    span_idx=seq,
                    char_start=offset,
                    char_end=offset + len(line),
                    seq=seq,
                )
            )
            seq += 1
        offset += len(line) + 1
    return spans


def build_case_graph(client: Any, case: dict, out_dir: Path, cfg: dict, *, workers: int, resume: bool) -> dict:
    if case["dataset"] == "turnabout":
        return build_turnabout_graph(client, case, out_dir, resume=resume)
    graph_path = out_dir / "graph.json"
    existing = load_json(graph_path)
    if existing:
        return existing
    out_dir.mkdir(parents=True, exist_ok=True)
    text = case["text"]
    if case.get("skip_pass1"):
        kept = synthetic_kept(text)
        dropped: list = []
        stats1 = {"skipped_grounding": 0, "kept_chars": sum(len(s.text) for s in kept)}
    else:
        kept, dropped, stats1 = run_pass1(
            text, config=cfg, client=client, out_dir=out_dir, resume=resume, workers=workers
        )
        (out_dir / "pass1" / "kept.jsonl").write_text(
            "\n".join(json.dumps(s.to_dict(), ensure_ascii=False) for s in kept) + "\n",
            encoding="utf-8",
        )
        failed_pass1 = int(stats1.get("failed_chunks") or 0)
        if failed_pass1:
            raise RuntimeError(f"pass1 incomplete: {failed_pass1} chunk(s) failed")
    kept_by_seq = {s.seq: s for s in kept}
    records = run_pass2(
        kept, config=cfg, client=client, out_dir=out_dir, resume=resume, workers=workers
    )
    failed_pass2 = [record for record in records if record.get("error")]
    if failed_pass2:
        raise RuntimeError(f"pass2 incomplete: {len(failed_pass2)} chunk(s) failed")
    nodes, edges, merge_stats = build_graph(records, kept_by_seq, max(len(text), 1))
    raw_graph = {"nodes": nodes, "edges": edges}
    try:
        repaired, coref_stats = repair_graph(
            raw_graph,
            [s.to_dict() for s in kept],
            client,
            out_dir,
            batch_size=8,
            window=1,
            resume=resume,
        )
        nodes, edges = repaired["nodes"], repaired["edges"]
    except Exception as exc:
        coref_stats = {"error": str(exc)}
        raise RuntimeError(f"coreference repair failed: {exc}") from exc
    try:
        consolidation_cfg = dict(cfg.get("entity_consolidation") or {})
        consolidated, consolidation_stats = consolidate_person_nodes(
            {"nodes": nodes, "edges": edges},
            client,
            out_dir,
            cap=int(consolidation_cfg.get("cap") or 360),
            batch_size=int(consolidation_cfg.get("batch_size") or 60),
            anchor_count=int(consolidation_cfg.get("anchor_count") or 12),
            resume=resume,
        )
        nodes, edges = consolidated["nodes"], consolidated["edges"]
    except Exception as exc:
        consolidation_stats = {"error": str(exc)}
        raise RuntimeError(f"person consolidation failed: {exc}") from exc
    kept_norm = [(row["seq"], row["text"]) for row in (s.to_dict() for s in kept)]
    for node in nodes:
        source_ids: list[int] = []
        for evidence in node.get("evidence", []):
            head = evidence[:40]
            for seq, row_text in kept_norm:
                if head and head in row_text:
                    source_ids.append(seq)
                    break
        node["source_sentence_ids"] = sorted(set(source_ids))[:10]
    graph = {
        "novel_chars": len(text),
        "pass1_stats": stats1,
        "merge_stats": merge_stats,
        "coref_stats": coref_stats,
        "consolidation_stats": consolidation_stats,
        "nodes": nodes,
        "edges": edges,
    }
    quality_cfg = dict(cfg.get("quality_gate") or {})
    quality = evaluate_graph_quality(
        graph,
        max_isolate_rate=float(quality_cfg.get("max_isolate_rate") or 0.60),
        min_edge_node_ratio=float(quality_cfg.get("min_edge_node_ratio") or 0.50),
        max_dropped_relation_rate=float(quality_cfg.get("max_dropped_relation_rate") or 0.55),
    )
    graph["quality"] = quality
    save_json(out_dir / "quality_report.json", quality)
    if bool(quality_cfg.get("enabled", False)) and not quality["passed"]:
        save_json(out_dir / "graph_rejected.json", graph)
        raise RuntimeError("graph quality gate failed: " + "; ".join(quality["failures"]))
    save_json(graph_path, graph)
    return graph


def build_turnabout_graph(client: Any, case: dict, out_dir: Path, *, resume: bool) -> dict:
    """Task-specific graph: evidence nodes, testimony nodes, and contradiction edges."""
    graph_path = out_dir / "graph.json"
    existing = load_json(graph_path)
    if existing:
        return existing
    out_dir.mkdir(parents=True, exist_ok=True)
    text = case["text"]
    evidence: dict[int, str] = {}
    testimony: dict[int, str] = {}
    mode = None
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("evidence"):
            mode = "evidence"
            continue
        if s.lower().startswith("testimon"):
            mode = "testimony"
            continue
        if mode == "evidence":
            m = re.match(r"\[(\d+)\]\s*Name:\s*(.+?)(?:\s*$)", s)
            if m:
                evidence[int(m.group(1))] = m.group(2).strip()
            m = re.match(r"\[(\d+)\]\s*Testimony:\s*(.+)", s)
            if m:
                testimony[int(m.group(1))] = m.group(2).strip()
        elif mode == "testimony":
            m = re.match(r"\[(\d+)\]\s*Testimony:\s*(.+)", s)
            if m:
                testimony[int(m.group(1))] = m.group(2).strip()
    # fallback: numbered lines after "Evidence:" / "Testimonies:"
    nodes: list[dict] = []
    for i, name in sorted(evidence.items()):
        nodes.append(
            {
                "id": f"ev_{i}",
                "name": f"Evidence {i}: {name}",
                "type": "clue_object",
                "salience": 4,
                "description": f"Evidence item #{i}",
                "attributes": {"ev_index": i},
                "evidence": [name],
            }
        )
    for j, t in sorted(testimony.items()):
        nodes.append(
            {
                "id": f"te_{j}",
                "name": f"Testimony {j}: {t}",
                "type": "evidence_sentence",
                "salience": 4,
                "description": f"Testimony item #{j}",
                "attributes": {"te_index": j},
                "evidence": [t],
            }
        )
    # LLM identifies contradiction pairs (grounded in the items)
    cache_path = out_dir / "contradictions.json"
    cached = load_json(cache_path) if resume else None
    if cached:
        pairs = cached
    else:
        ev_lines = "\n".join(f"[{i}] {name}" for i, name in sorted(evidence.items()))
        te_lines = "\n".join(f"[{j}] {t}" for j, t in sorted(testimony.items()))
        prompt = (
            f"Evidence items:\n{ev_lines}\n\nTestimony items:\n{te_lines}\n\n"
            "Identify up to 5 pairs (evidence_index, testimony_index) whose contents contradict each other "
            "in a way that reveals a lie or inconsistency. Include any pair that is plausibly contradictory; "
            "the true pair is among them. Return strict JSON only: "
            '{"pairs": [{"evidence": 0, "testimony": 0, "reason": "short reason"}]}'
        )
        payload = client.complete_json(
            "You are an expert at spotting contradictions in courtroom testimony.", prompt, max_tokens=2000
        )
        pairs = [p for p in (payload.get("pairs") if isinstance(payload, dict) else payload or []) if isinstance(p, dict)]
        save_json(cache_path, pairs)
    edges: list[dict] = []
    for p in pairs:
        ei, tj = p.get("evidence"), p.get("testimony")
        if ei is None or tj is None:
            continue
        src = next((n["id"] for n in nodes if n["attributes"].get("ev_index") == int(ei)), None)
        tgt = next((n["id"] for n in nodes if n["attributes"].get("te_index") == int(tj)), None)
        if src and tgt:
            edges.append(
                {
                    "source": src,
                    "target": tgt,
                    "type": "contradicts",
                    "evidence": str(p.get("reason") or "")[:200],
                    "confidence": 0.9,
                    "importance": 5,
                }
            )
    graph = {"novel_chars": len(text), "nodes": nodes, "edges": edges, "turnabout_pairs": pairs}
    save_json(graph_path, graph)
    return graph


def answer_full(client: Any, q: dict, text: str, out_dir: Path, key: str, *, full_client: Any | None = None) -> str:
    path = out_dir / f"full_{key}.json"
    cached = load_json(path)
    if cached:
        return cached["answer"]
    caller = full_client or client
    opt = options_block(q)
    if q.get("answer_format") == "pair":
        prompt = (
            f"Task: {q['question']}\n\nMaterials:\n{text}\n\n"
            'Answer strictly with JSON: {"evidence": <index>, "testimony": <index>} '
            "for the ONE pair that contradicts each other. Base the answer only on the materials."
        )
    else:
        instruction = (
            "Answer with the option letter and its text (e.g. 'C. Through the window'). "
            "If undecidable, say 'unknown'."
        )
        if q.get("meta", {}).get("dataset") == "detectbench":
            instruction = (
                "First identify the implicit evidence pieces in the text that bear on the question, "
                "then answer with the option letter and its text."
            )
        prompt = (
            f"Question: {q['question']}\n\n{opt}\n\nText:\n{text}\n\n"
            + instruction
        )
    raw = ""
    error = ""
    for _ in range(3):
        try:
            raw = caller.complete("You are a careful detective-novel reader using ONLY the provided text.", prompt, max_tokens=4000)
            if raw.strip():
                break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    save_json(path, {"answer": raw, "error": error})
    return raw


def answer_graph(client: Any, q: dict, store: GraphStore, kept: list[dict], out_dir: Path, key: str) -> str:
    graph_fp = hashlib.sha1(",".join(sorted(n["id"] for n in store.nodes)).encode("utf-8")).hexdigest()[:8]
    path = out_dir / f"graph_{key}_{graph_fp}.json"
    cached = load_json(path)
    if cached:
        return cached["answer"]
    if q.get("answer_format") == "pair":
        # Task-specific retrieval: surface the contradiction edges from the graph
        contra = [
            e
            for e in store.edges
            if e.get("type") == "contradicts"
        ]
        clue_lines = []
        for e in contra[:10]:
            src = store.by_id.get(e["source"], {})
            tgt = store.by_id.get(e["target"], {})
            clue_lines.append(
                f"- {src.get('name', e['source'])}  <->  {tgt.get('name', e['target'])}  ({e.get('evidence', '')[:120]})"
            )
        if not clue_lines:
            clue_lines = ["(no contradiction edges found in graph)"]
        opt = ""
        prompt = (
            f"Task: {q['question']}\n\nGraph contradiction candidates:\n" + "\n".join(clue_lines)
            + "\n\nPick the ONE true pair. Return strict JSON only: "
            '{"evidence": <evidence index>, "testimony": <testimony index>}'
        )
        raw = ""
        error = ""
        for _ in range(3):
            try:
                raw = client.complete(
                    "You are a careful detective-novel reader answering from the provided clues only.", prompt, max_tokens=4000
                )
                if raw.strip():
                    break
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        save_json(path, {"answer": raw, "error": error})
        return raw
    plan = plan_search(client, q["question"], store, cache_dir=out_dir / "plans", graph_fp=graph_fp)
    first, second = execute(store, plan)
    sentence_index = BM25Index([row["text"] for row in kept])
    sent_hits = top_sentences(sentence_index, q["question"], plan, k=6)
    sent_texts = [kept[i]["text"] for i, _, _ in sent_hits]
    clue_lines = []
    for node_id in first[:6]:
        node = store.by_id[node_id]
        clue_lines.append(
            f"- {node['name']} [{node['type']}]: {node.get('description','')} | {node.get('evidence', [''])[0][:100]}".strip()
        )
    opt = options_block(q)
    if q.get("answer_format") == "pair":
        prompt = (
            f"Task: {q['question']}\n\nGraph clues:\n" + "\n".join(clue_lines)
            + "\n\nEvidence sentences:\n" + "\n".join(f"- {t}" for t in sent_texts[:6])
            + "\n\nTreat decoy clues (e.g. a half-open door used to create an illusion) as misdirection. "
            'Answer strictly with JSON: {"evidence": <index>, "testimony": <index>} '
            "for the ONE pair that contradicts each other."
        )
    else:
        prompt = (
            f"Question: {q['question']}\n\n{opt}\n\nGraph clues:\n" + "\n".join(clue_lines)
            + "\n\nEvidence sentences:\n" + "\n".join(f"- {t}" for t in sent_texts[:6])
            + "\n\nTreat illusion/decoy clues (e.g. a half-open door) as misdirection. "
            "Answer with the option letter and its text."
        )
    raw = ""
    error = ""
    for _ in range(3):
        try:
            raw = client.complete(
                "You are a careful detective-novel reader answering from the provided clues only.", prompt, max_tokens=4000
            )
            if raw.strip():
                break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    save_json(path, {"answer": raw, "error": error})
    return raw


def judge_letter(client: Any, q: dict, answer: str, out_dir: Path, key: str) -> tuple[bool, str]:
    path = out_dir / f"j_{key}.json"
    cached = load_json(path)
    if cached:
        return bool(cached["correct"]), str(cached["note"])
    gold = q.get("gold_text") or ""
    payload = client.complete_json(
        "You are a strict but fair answer judge for a detective-novel QA benchmark.",
        (
            f"Question: {q['question']}\nGold answer: {gold}\nModel answer: {answer}\n"
            'Return strict JSON only: {"correct": true/false, "note": "one short reason"}'
        ),
    )
    verdict = {
        "correct": bool(payload.get("correct")) if isinstance(payload, dict) else False,
        "note": str(payload.get("note") or "") if isinstance(payload, dict) else "",
    }
    save_json(path, verdict)
    return verdict["correct"], verdict["note"]


def judge_pair(answer: str, gold_pairs: list[list[int]]) -> tuple[bool, bool, bool]:
    try:
        parsed = extract_json(answer)
        ev = int(parsed.get("evidence", -1))
        te = int(parsed.get("testimony", -1))
    except Exception:
        m = re.search(r'"evidence"\s*:\s*(\d+).*?"testimony"\s*:\s*(\d+)', answer, re.S)
        if not m:
            return False, False, False
        ev, te = int(m.group(1)), int(m.group(2))
    pairs = [tuple(p) for p in (gold_pairs or [])]
    return (ev, te) in pairs, any(ev == e for e, _ in pairs), any(te == t for _, t in pairs)


def run_case(
    client: Any,
    build_client: Any,
    full_client: Any,
    case: dict,
    cfg: dict,
    *,
    workers: int,
    resume: bool,
    max_chars: int | None,
    build_graphs: bool,
    mask_detailed: bool = False,
) -> dict:
    case_id = case["case_id"]
    out_dir = OUT / case["dataset"] / case_id
    graph: dict | None = None
    kept = []
    prebuilt_graph_path = None
    if case["dataset"] == "detectiveqa":
        novel_cfg = (cfg.get("novels") or {}).get(str(case["meta"].get("novel_id")))
        if novel_cfg:
            candidate = _resolve(ROOT, str(novel_cfg["output_dir"])) / "graph.json"
            if candidate.exists():
                prebuilt_graph_path = candidate
    if prebuilt_graph_path is not None:
        graph = json.loads(prebuilt_graph_path.read_text(encoding="utf-8"))
        kept_path = prebuilt_graph_path.parent / "pass1" / "kept.jsonl"
        if kept_path.exists():
            kept = [
                json.loads(line)
                for line in kept_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    elif build_graphs:
        try:
            graph = build_case_graph(build_client, case, out_dir, cfg, workers=workers, resume=resume)
        except Exception as exc:
            print(f"[warn] graph build failed for {case_id}: {type(exc).__name__}: {exc}")
    elif (out_dir / "graph.json").exists():
        graph = json.loads((out_dir / "graph.json").read_text(encoding="utf-8"))
    if graph is not None:
        store = GraphStore(graph["nodes"], graph["edges"])
        if not kept:
            kept_path = out_dir / "pass1" / "kept.jsonl"
            if kept_path.exists():
                kept = [
                    json.loads(line)
                    for line in kept_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
        if not kept:
            kept = [{"seq": i, "text": line} for i, line in enumerate(case["text"].splitlines()) if line.strip()]
    else:
        store = None
    rows = []
    for qi, q in enumerate(case["questions"]):
        text = case["text"]
        if case["dataset"] == "detectiveqa" and mask_detailed and q.get("mask_char") is not None:
            text = text[: q["mask_char"]]
        if max_chars is not None and len(text) > max_chars:
            text = text[:max_chars]
        key = hashlib.sha1(
            f"{case_id}|{qi}|{getattr(client, 'model', '?')}|{max_chars}|{PROMPT_VERSION}".encode("utf-8")
        ).hexdigest()[:10]
        ans_full = answer_full(client, q, text, out_dir, key, full_client=full_client)
        row = {"qid": q["qid"], "question": q["question"], "gold_text": q.get("gold_text"), "gold_index": q.get("gold_index")}
        if q.get("answer_format") == "pair":
            row["gold_pairs"] = q.get("gold_pairs")
            correct, ev_ok, te_ok = judge_pair(ans_full, q.get("gold_pairs") or [])
            row["full_text"] = {"answer": ans_full, "pair": correct, "evidence": ev_ok, "testimony": te_ok}
        else:
            try:
                correct, note = judge_letter(client, q, ans_full, out_dir, f"full_{key}")
            except Exception as exc:
                correct, note = False, f"judge failed: {type(exc).__name__}"
            row["full_text"] = {"answer": ans_full, "correct": correct, "note": note}
        if store is not None:
            try:
                ans_graph = answer_graph(client, q, store, kept, out_dir, key)
            except Exception as exc:
                ans_graph = ""
                graph_error = f"graph answer failed: {type(exc).__name__}: {exc}"
            else:
                graph_error = ""
            if q.get("answer_format") == "pair":
                if graph_error:
                    row["graph"] = {"answer": "", "pair": False, "evidence": False, "testimony": False, "error": graph_error}
                else:
                    correct, ev_ok, te_ok = judge_pair(ans_graph, q.get("gold_pairs") or [])
                    row["graph"] = {"answer": ans_graph, "pair": correct, "evidence": ev_ok, "testimony": te_ok}
            else:
                if graph_error:
                    row["graph"] = {"answer": "", "correct": False, "note": graph_error}
                else:
                    try:
                        correct, note = judge_letter(client, q, ans_graph, out_dir, f"graph_{key}")
                    except Exception as exc:
                        correct, note = False, f"judge failed: {type(exc).__name__}"
                    row["graph"] = {"answer": ans_graph, "correct": correct, "note": note}
        rows.append(row)
        print(
            f"[{case_id}] Q{qi} full={'对' if row['full_text'].get('correct', row['full_text'].get('pair')) else '错'} "
            f"graph={'-' if 'graph' not in row else ('对' if row['graph'].get('correct', row['graph'].get('pair')) else '错')}"
        )
    return {"case_id": case_id, "title": case["title"], "dataset": case["dataset"], "rows": rows}


def summarize(results: list[dict], dataset: str) -> dict:
    full_correct = full_total = graph_correct = graph_total = 0
    for result in results:
        for row in result["rows"]:
            ft = row["full_text"]
            full_total += 1
            full_correct += 1 if ft.get("correct", ft.get("pair")) else 0
            if "graph" in row:
                g = row["graph"]
                graph_total += 1
                graph_correct += 1 if g.get("correct", g.get("pair")) else 0
    return {
        "dataset": dataset,
        "full_text": {"correct": full_correct, "total": full_total},
        "graph": {"correct": graph_correct, "total": graph_total},
    }


def main() -> None:
    parser = argpar
