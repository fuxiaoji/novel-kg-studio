"""C13c: single-pass option-conditioned graph rebuttal with DeepSeek V4 Flash (no reasoning mode)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from analyze_dense_retrieval import chunk_embeddings, embed  # noqa: E402
from build_c_next10_graphs import merged_cases  # noqa: E402
from c13_option_rebuttal import _option_packet  # noqa: E402
from c8_graph_passage import C8Context, LETTERS, normalize_letter, retrieve_bm25  # noqa: E402
from novel_kg_studio.llm import extract_json  # noqa: E402
from run_c8_20 import FIRST10, NOVELS, graph_path  # noqa: E402

BASE = ROOT / "outputs" / "four_datasets"
VERSION = "c13c-v4flash-no-reasoning-option-graph-rebuttal-v1"
VERSION_NOGRAPH = "c13c-v4flash-no-reasoning-option-dense-bm25-rebuttal-v1"
VERSION_OVERLAY = "c13d-v4flash-option-rebuttal-safe-graph-overlay-v1"


class DeepSeekNoThinkingClient:
    """DeepSeek client that explicitly disables thinking mode.

    V4 Flash can return an empty assistant content when the thinking switch is
    omitted.  Keeping the switch here also makes the experimental condition
    auditable instead of relying on an API default.
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")

    def complete_json(self, system: str, user: str, *, max_tokens: int = 300) -> Any:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_error = ""
        for attempt in range(5):
            request = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            )
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    data = json.loads(response.read().decode("utf-8"))
                content = (data["choices"][0]["message"] or {}).get("content") or ""
                if not content.strip():
                    raise RuntimeError("empty completion")
                return extract_json(content)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code in (400, 401, 402, 403, 404):
                    break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(min(30, 2 ** attempt))
        raise RuntimeError(last_error)


def _option_packet_no_graph(
    ctx: C8Context, matrix: np.ndarray, query_vector: np.ndarray, q: dict[str, Any], option_index: int
) -> dict[str, Any]:
    option = q["choices"][option_index]
    local_q = {"question": q["question"], "choices": [option]}
    bm25 = [int(x) for x in retrieve_bm25(ctx, local_q, limit=36)["diagnostics"]["selected"]]
    dense_scores = matrix @ query_vector
    dense = list(map(int, np.argsort(dense_scores)[::-1][:80]))
    bm_rank = {index: rank for rank, index in enumerate(bm25)}
    dense_rank = {index: rank for rank, index in enumerate(dense)}
    candidates = set(bm25) | set(dense)
    rrf = {
        index: 1.0 / (40 + bm_rank.get(index, 10**6)) + 1.2 / (40 + dense_rank.get(index, 10**6))
        for index in candidates
    }
    selected: list[int] = []
    for index in sorted(candidates, key=lambda item: rrf[item], reverse=True):
        if any(abs(index - old) <= 1 for old in selected):
            continue
        selected.append(index)
        if len(selected) >= 5:
            break
    chunks = [
        {
            "id": ctx.base.chunks[index].id,
            "index": index,
            "start": ctx.base.chunks[index].start,
            "end": ctx.base.chunks[index].end,
            "text": ctx.base.chunks[index].text,
            "rrf_score": rrf[index],
        }
        for index in selected
    ]
    return {"letter": LETTERS[option_index], "option": option, "chunks": chunks, "links": []}


def answer_one(
    client: Any,
    q: dict[str, Any],
    ctx: C8Context,
    matrix: Any,
    *,
    variant: str = "graph",
    version: str = VERSION,
) -> dict[str, Any]:
    queries = [f"Question: {q['question']}\nCandidate answer: {choice}" for choice in q["choices"][:4]]
    query_matrix = embed(queries)
    if variant == "graph":
        packets = [_option_packet(ctx, matrix, query_matrix[index], q, index) for index in range(4)]
    else:
        packets = [_option_packet_no_graph(ctx, matrix, query_matrix[index], q, index) for index in range(4)]
        if variant == "graph_overlay":
            graph_packets = [_option_packet(ctx, matrix, query_matrix[index], q, index) for index in range(4)]
            for packet, graph_packet in zip(packets, graph_packets):
                packet["links"] = graph_packet["links"][:2]
    # Keep the three strongest passages per option. Repeated passages retain
    # every option label so the reader can see which claims retrieved them.
    chunks: dict[str, dict[str, Any]] = {}
    for packet in packets:
        for rank, row in enumerate(packet["chunks"][:3]):
            item = chunks.setdefault(row["id"], {**row, "for_options": [], "best_rank": rank})
            item["for_options"].append(packet["letter"])
            item["best_rank"] = min(item["best_rank"], rank)
    ordered = sorted(chunks.values(), key=lambda row: (row["best_rank"], row["start"]))
    passages = "\n\n".join(
        f"[{row['id']} | retrieved for option(s) {','.join(sorted(set(row['for_options'])))}]\n{row['text']}"
        for row in ordered
    )
    paths = []
    for packet in packets:
        for link in packet["links"][:2]:
            path = (
                f"- option {packet['letter']}: {link['source']} --{link['relation']}--> "
                f"{link['target']} [{link['chunk_id']}]"
            )
            if variant == "graph_overlay" and link.get("evidence"):
                path += f"; grounded edge evidence: {str(link['evidence'])[:350]}"
            paths.append(path)
    options = "\n".join(f"{LETTERS[index]}. {choice}" for index, choice in enumerate(q["choices"][:4]))
    prompt = (
        f"QUESTION\n{q['question']}\n\nOPTIONS\n{options}\n\n"
        f"OPTION-CONDITIONED GRAPH-RETRIEVED PASSAGES\n{passages}"
        + (f"\n\nGRAPH RELATION PATHS (navigation hints; passage text has authority)\n" + "\n".join(paths) if paths else "")
        + "\n\nEvaluate every option as a claim. Look specifically for a passage that directly refutes each option by naming "
        "a different person, number, place, time, action, or motive. Absence is unknown, not refutation. Also identify direct "
        "support. Resolve machine-translated aliases by event semantics. For NOT/EXCEPT/incorrect questions, obey the negative "
        "wording. Select the option with the strongest net textual support after eliminating only explicitly contradicted "
        "claims. Return strict JSON only with no chain of thought: "
        '{"selected_letter":"A|B|C|D","support_quote":"short verbatim quote","rebutted_options":["A"]}'
    )
    raw = client.complete_json(
        "You answer a detective-novel multiple-choice question from compact graph-retrieved evidence. Output only JSON.",
        prompt,
        max_tokens=300,
    )
    letter = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else raw)
    return {
        "method": {
            "graph": "c13c_single_pass_option_graph_rebuttal",
            "no_graph": "c13c_option_dense_bm25_rebuttal_ablation",
            "graph_overlay": "c13d_option_rebuttal_safe_graph_overlay",
        }[variant],
        "selected_letter": letter,
        "support_quote": str(raw.get("support_quote", "")) if isinstance(raw, dict) else "",
        "rebutted_options": raw.get("rebutted_options", []) if isinstance(raw, dict) else [],
        "raw": raw,
        "retrieval": {"chunks": ordered, "paths": paths, "option_chunk_ids": {p["letter"]: [c["id"] for c in p["chunks"][:3]] for p in packets}},
        "prompt_version": version,
        "mask": "unmasked",
        "graph_enabled": variant != "no_graph",
        "variant": variant,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--ablation", choices=("graph", "no_graph", "graph_overlay"), default="graph")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    use_graph = args.ablation != "no_graph"
    version = {"graph": VERSION, "no_graph": VERSION_NOGRAPH, "graph_overlay": VERSION_OVERLAY}[args.ablation]
    if args.out is None:
        args.out = BASE / {
            "graph": "dqa_deepseek_c13c_20",
            "no_graph": "dqa_deepseek_c13c_nograph20",
            "graph_overlay": "dqa_deepseek_c13d_overlay20",
        }[args.ablation]
    args.out.mkdir(parents=True, exist_ok=True)
    cases = merged_cases(args.novels)
    client = DeepSeekNoThinkingClient(args.model)
    total = sum(len(cases[n]["questions"]) for n in args.novels)
    done = 0; started = time.time(); lock = threading.Lock()
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "version": version, "model": args.model, "thinking": "disabled", "mask": "unmasked", "graph_enabled": use_graph, "variant": args.ablation, "novels": args.novels}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    for novel in args.novels:
        case = cases[novel]
        graph = json.loads(graph_path(novel).read_text(encoding="utf-8"))
        ctx = C8Context.build(graph, case["text"], None)
        matrix = chunk_embeddings(novel, [chunk.text for chunk in ctx.base.chunks])
        jobs = []
        for qi, q in enumerate(case["questions"]):
            path = args.out / "answers" / novel / f"q{qi:02d}.json"
            if path.exists():
                try:
                    row = json.loads(path.read_text(encoding="utf-8"))
                    if row.get("prompt_version") == version and normalize_letter(row.get("selected_letter")) in LETTERS:
                        with lock: done += 1
                        continue
                except Exception:
                    pass
            jobs.append((qi, q, path))

        def one(job: tuple[int, dict[str, Any], Path]) -> dict[str, Any]:
            qi, q, path = job
            row = answer_one(client, q, ctx, matrix, variant=args.ablation, version=version)
            row.update({"novel": novel, "batch": "first10" if novel in FIRST10 else "second10", "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_letter": LETTERS[q["gold_index"]], "correct": row["selected_letter"] == LETTERS[q["gold_index"]], "answer_model": args.model})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
            return row

        with ThreadPoolExecutor(max_workers=min(args.workers, len(jobs) or 1)) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            for future in as_completed(futures):
                row = future.result()
                with lock:
                    done += 1; elapsed = max(time.time() - started, 0.01)
                    progress = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "completed": done, "total": total, "current": f"c13c/{novel}/q{row['qi']} -> {row['selected_letter']}", "per_hour": done / elapsed * 3600, "eta_minutes": (total - done) / max(done / elapsed, 1e-9) / 60}
                    (args.out / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
                    print(f"[{done}/{total}] c13c/{novel}/q{row['qi']} -> {row['selected_letter']}", flush=True)

    selected = set(args.novels)
    outputs = [json.loads(path.read_text(encoding="utf-8")) for path in (args.out / "answers").glob("*/*.json") if path.parent.name in selected]
    correct = sum(bool(row["correct"]) for row in outputs)
    print(json.dumps({"correct": correct, "total": len(outputs), "accuracy": correct / len(outputs)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
