"""Run C1-C4 and C6 on the ten existing option-C graphs, never rebuilding them."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from c_option_methods import LETTERS, normalize_letter, run_method
from eval_four_datasets import OllamaClient, load_cases

NOVELS = ["26", "27", "28", "30", "31", "33", "40", "53", "56", "79"]
METHOD_SOURCE_HASH = hashlib.sha1((ROOT / "scripts" / "c_option_methods.py").read_bytes()).hexdigest()[:12]


class ProgressReporter:
    """Publish a file-based live dashboard after every completed answer."""

    def __init__(self, out_root: Path, cases: dict[str, dict], methods: list[str], novels: list[str], max_questions: int, masks: list[str] | None = None) -> None:
        self.out_root = out_root
        self.lock = threading.Lock()
        self.started = time.time()
        self.masks = masks or ["masked", "unmasked"]
        self.total = sum(min(len(cases[n]["questions"]), max_questions or 10**9) for n in novels) * len(self.masks) * len(methods)
        self.completed = 0
        self.correct = 0
        self.unparsed = 0
        self.current = "starting"
        self.by_method: dict[str, dict[str, int]] = {
            f"{m}/{mask}": {"done": 0, "correct": 0, "unparsed": 0}
            for m in methods
            for mask in self.masks
        }
        self._write_html()
        self.publish()

    def _write_html(self) -> None:
        html = """<!doctype html><meta charset="utf-8"><title>方案 C 改进实验实时进度</title>
<style>body{font-family:system-ui;margin:28px;max-width:1000px} .bar{height:22px;background:#ddd;border-radius:12px;overflow:hidden}.fill{height:100%;background:#3578e5}table{border-collapse:collapse;width:100%;margin-top:20px}td,th{padding:7px;border-bottom:1px solid #ddd;text-align:left}.muted{color:#666}</style>
<h1>方案 C 改进实验</h1><div id="app">正在加载……</div>
<script src="progress.js"></script><script>
function draw(){let p=window.C_PROGRESS||{};let pct=p.total?100*p.completed/p.total:0;let rows=Object.entries(p.by_method||{}).map(([k,v])=>`<tr><td>${k}</td><td>${v.done}</td><td>${v.correct}</td><td>${v.done?(100*v.correct/v.done).toFixed(1):'-'}%</td><td>${v.unparsed}</td></tr>`).join('');document.getElementById('app').innerHTML=`<p><b>${p.completed||0} / ${p.total||0}</b>（${pct.toFixed(1)}%）</p><div class="bar"><div class="fill" style="width:${pct}%"></div></div><p>当前：${p.current||''}</p><p>总字母正确率：${p.completed?(100*p.correct/p.completed).toFixed(1):'-'}%；未解析：${p.unparsed||0}；速度：${(p.per_hour||0).toFixed(1)} 题/小时；预计剩余：${p.eta||'-'}</p><p class="muted">更新时间：${p.updated||''}</p><table><tr><th>方案</th><th>完成</th><th>正确</th><th>正确率</th><th>未解析</th></tr>${rows}</table>`}
draw();setInterval(()=>{let s=document.createElement('script');s.src='progress.js?t='+Date.now();s.onload=draw;document.head.appendChild(s)},3000)
</script>"""
        (self.out_root / "progress.html").write_text(html, encoding="utf-8")

    def seed_cached(self, method: str, mask: str, row: dict) -> None:
        self._record(method, mask, row, publish=False)

    def record(self, method: str, mask: str, row: dict, current: str) -> None:
        with self.lock:
            self.current = current
            self._record(method, mask, row, publish=True)

    def _record(self, method: str, mask: str, row: dict, publish: bool) -> None:
        letter = normalize_letter(row.get("selected_letter"))
        gold = row.get("gold_index")
        correct = bool(letter and isinstance(gold, int) and 0 <= gold < 4 and letter == LETTERS[gold])
        key = f"{method}/{mask}"
        self.completed += 1
        self.correct += int(correct)
        self.unparsed += int(letter is None)
        self.by_method[key]["done"] += 1
        self.by_method[key]["correct"] += int(correct)
        self.by_method[key]["unparsed"] += int(letter is None)
        if publish:
            self.publish()

    def publish(self) -> None:
        elapsed = max(time.time() - self.started, 0.001)
        per_hour = self.completed / elapsed * 3600
        remaining = max(self.total - self.completed, 0)
        eta_seconds = remaining / per_hour * 3600 if per_hour else 0
        payload = {
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "completed": self.completed,
            "total": self.total,
            "correct": self.correct,
            "unparsed": self.unparsed,
            "current": self.current,
            "per_hour": per_hour,
            "eta": f"{eta_seconds / 3600:.1f} 小时" if remaining else "已完成",
            "by_method": self.by_method,
        }
        (self.out_root / "progress.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        (self.out_root / "progress.js").write_text("window.C_PROGRESS=" + json.dumps(payload, ensure_ascii=False) + ";", encoding="utf-8")


def merged_cases() -> dict[str, dict]:
    by_novel: dict[str, dict] = {}
    for case in load_cases("detectiveqa"):
        nid = str(case["meta"].get("novel_id"))
        if nid not in NOVELS:
            continue
        merged = by_novel.setdefault(nid, {"text": case["text"], "questions": []})
        seen = {q["question"] for q in merged["questions"]}
        for q in case["questions"]:
            if q["question"] not in seen:
                seen.add(q["question"])
                merged["questions"].append(q)
    return by_novel


def graph_manifest(graph_root: Path, novels: list[str]) -> dict[str, dict]:
    manifest = {}
    for nid in novels:
        path = graph_root / "novels" / nid / "graph.json"
        if not path.exists():
            raise FileNotFoundError(f"--reuse-graph-only: missing {path}")
        data = path.read_bytes()
        manifest[nid] = {
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mtime_ns": path.stat().st_mtime_ns,
            "bytes": len(data),
        }
    return manifest


def output_path(out_root: Path, method: str, mask: str, nid: str, qi: int) -> Path:
    return out_root / method / mask / nid / f"q{qi:02d}.json"


def valid_cached(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        if normalize_letter(row.get("selected_letter")) not in LETTERS:
            return False
        if row.get("mask") == "unmasked" and row.get("prompt_version") == "c-improvements-v1":
            return True
        return row.get("mask_policy") == "strict-source-filtered-graph-v1" and row.get("prompt_hash") == METHOD_SOURCE_HASH
    except Exception:
        return False


def run_independent(args: argparse.Namespace, cases: dict[str, dict], before: dict[str, dict], reporter: ProgressReporter) -> None:
    client = OllamaClient(args.answer_model, max_tokens=3000, num_ctx=32768)
    for method in args.methods:
        for mask in args.masks:
            for nid in args.novels:
                graph_path = Path(before[nid]["path"])
                graph = json.loads(graph_path.read_text(encoding="utf-8"))
                case = cases[nid]
                jobs = []
                for qi, q in enumerate(case["questions"][: args.max_questions or None]):
                    path = output_path(args.out_root, method, mask, nid, qi)
                    if valid_cached(path):
                        reporter.seed_cached(method, mask, json.loads(path.read_text(encoding="utf-8")))
                        continue
                    jobs.append((qi, q, path))
                if not jobs:
                    print(f"[{method}/{mask}/{nid}] cached", flush=True)
                    continue
                t0 = time.time()

                def one(job: tuple[int, dict, Path]) -> tuple[int, Path, dict]:
                    qi, q, path = job
                    mask_char = q.get("mask_char") if mask == "masked" else None
                    result = run_method(method, client, q, graph, case["text"], mask_char)
                    result.update(
                        {
                            "novel": nid,
                            "qi": qi,
                            "qid": q["qid"],
                            "question": q["question"],
                            "choices": q["choices"],
                            "gold_index": q.get("gold_index"),
                            "gold_text": q.get("gold_text"),
                            "mask": mask,
                            "answer_model": args.answer_model,
                        }
                    )
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
                    return qi, path, result

                done = 0
                with ThreadPoolExecutor(max_workers=min(args.answer_workers, len(jobs))) as pool:
                    futures = [pool.submit(one, job) for job in jobs]
                    for fut in as_completed(futures):
                        qi, _, result = fut.result()
                        done += 1
                        print(
                            f"[{method}/{mask}/{nid}] {done}/{len(jobs)} q{qi} -> {result.get('selected_letter')} "
                            f"({time.time() - t0:.0f}s)",
                            flush=True,
                        )
                        reporter.record(method, mask, result, f"{method} / {mask} / 小说 {nid} / q{qi}")


def _compact_candidate(row: dict) -> dict:
    return {
        "method": row.get("method"),
        "selected_letter": row.get("selected_letter"),
        "confidence": row.get("confidence"),
        "evidence_ids": row.get("evidence_ids", []),
        "reason": row.get("reason", ""),
        "evidence": row.get("evidence", {}),
    }


def run_arbiter(args: argparse.Namespace, cases: dict[str, dict], reporter: ProgressReporter) -> None:
    client = OllamaClient(args.answer_model, max_tokens=2200, num_ctx=32768)
    for mask in ("masked", "unmasked"):
        for nid in args.novels:
            questions = cases[nid]["questions"][: args.max_questions or None]
            for qi, q in enumerate(questions):
                out = output_path(args.out_root, "c6", mask, nid, qi)
                if valid_cached(out):
                    reporter.seed_cached("c6", mask, json.loads(out.read_text(encoding="utf-8")))
                    continue
                candidates = []
                for method in ("c1", "c2", "c3", "c4"):
                    p = output_path(args.out_root, method, mask, nid, qi)
                    if not p.exists():
                        raise FileNotFoundError(f"C6 requires {p}")
                    candidates.append(_compact_candidate(json.loads(p.read_text(encoding="utf-8"))))
                letters = [normalize_letter(r["selected_letter"]) for r in candidates]
                confident_agreement = len(set(letters)) == 1 and letters[0] is not None and letters[0] in LETTERS and all(
                    r["confidence"] in {"high", "medium"} and r["evidence_ids"] for r in candidates
                )
                if confident_agreement:
                    winner = letters[0]
                    payload = {
                        "selected_letter": winner,
                        "selected_text": q["choices"][LETTERS.index(winner)],
                        "confidence": "high",
                        "evidence_ids": sorted({x for r in candidates for x in r["evidence_ids"]}),
                        "reason": "All four independently grounded methods agree.",
                        "arbitrated": False,
                    }
                else:
                    prompt = (
                        f"Question: {q['question']}\nOptions:\n"
                        + "\n".join(f"{LETTERS[i]}. {v}" for i, v in enumerate(q["choices"][:4]))
                        + "\n\nIndependent candidates and their evidence summaries:\n"
                        + json.dumps(candidates, ensure_ascii=False)
                        + '\n\nDo not vote by count. Prefer explicit, relevant evidence; absence is not contradiction. Return strict JSON: '
                        + '{"selected_letter":"A|B|C|D","confidence":"high|medium|low","reason":"brief","evidence_ids":[]}'
                    )
                    raw = client.complete_json("You arbitrate only from the supplied evidence summaries; never use a gold answer.", prompt, max_tokens=1800)
                    winner = normalize_letter(raw.get("selected_letter") if isinstance(raw, dict) else None)
                    if winner is None or winner not in LETTERS:
                        # Deterministic confidence fallback, not a silent majority vote.
                        ranked = sorted(
                            candidates,
                            key=lambda r: ({"high": 2, "medium": 1, "low": 0}.get(r["confidence"], 0), len(r["evidence_ids"])),
                            reverse=True,
                        )
                        winner = normalize_letter(ranked[0]["selected_letter"]) or "A"
                    payload = {
                        "selected_letter": winner,
                        "selected_text": q["choices"][LETTERS.index(winner)],
                        "confidence": str(raw.get("confidence", "low")) if isinstance(raw, dict) else "low",
                        "evidence_ids": raw.get("evidence_ids", []) if isinstance(raw, dict) else [],
                        "reason": str(raw.get("reason", "")) if isinstance(raw, dict) else "",
                        "arbitrated": True,
                        "raw": raw,
                    }
                payload.update({"method": "c6_evidence_arbiter", "novel": nid, "qi": qi, "qid": q["qid"], "question": q["question"], "choices": q["choices"], "gold_index": q.get("gold_index"), "gold_text": q.get("gold_text"), "mask": mask, "candidates": candidates, "prompt_hash": METHOD_SOURCE_HASH})
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"[c6/{mask}/{nid}] q{qi} -> {winner}", flush=True)
                reporter.record("c6", mask, payload, f"c6 / {mask} / 小说 {nid} / q{qi}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c")
    parser.add_argument("--out-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_improvements")
    parser.add_argument("--reuse-graph-only", action="store_true", default=True)
    parser.add_argument("--methods", nargs="+", choices=["c1", "c2", "c3", "c4"], default=["c1", "c2", "c3", "c4"])
    parser.add_argument("--masks", nargs="+", choices=["masked", "unmasked"], default=["masked", "unmasked"])
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--answer-model", default="qwen2.5:7b-32k")
    parser.add_argument("--answer-workers", type=int, default=3)
    parser.add_argument("--max-questions", type=int, default=0)
    parser.add_argument("--arbiter-only", action="store_true")
    args = parser.parse_args()
    args.out_root.mkdir(parents=True, exist_ok=True)
    cases = merged_cases()
    before = graph_manifest(args.graph_root, args.novels)
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "graph_root": str(args.graph_root),
        "out_root": str(args.out_root),
        "reuse_graph_only": True,
        "answer_model": args.answer_model,
        "methods": args.methods,
        "masks": args.masks,
        "graphs_before": before,
    }
    (args.out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.arbiter_only:
        reporter = ProgressReporter(args.out_root, cases, ["c6"], args.novels, args.max_questions, args.masks)
        run_arbiter(args, cases, reporter)
    else:
        reporter = ProgressReporter(args.out_root, cases, args.methods, args.novels, args.max_questions, args.masks)
        run_independent(args, cases, before, reporter)
    after = graph_manifest(args.graph_root, args.novels)
    if before != after:
        raise RuntimeError("Read-only graph invariant violated: graph hashes or timestamps changed")
    manifest["graphs_after"] = after
    manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
    (args.out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
