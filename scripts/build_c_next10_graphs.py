"""Build option-C graphs once for the second ten DetectiveQA novels with live progress."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import threading
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from eval_four_datasets import OllamaClient, build_case_graph, load_cases

NOVELS = ["15", "16", "25", "29", "81", "82", "83", "84", "87", "90"]


def merged_cases(novels: list[str]) -> dict[str, dict]:
    wanted = set(novels)
    merged: dict[str, dict] = {}
    for case in load_cases("detectiveqa"):
        novel = str(case["meta"].get("novel_id"))
        if novel not in wanted:
            continue
        row = merged.setdefault(
            novel,
            {"dataset": "detectiveqa", "case_id": f"detectiveqa_{novel}", "title": case["title"], "text": case["text"], "meta": {"novel_id": novel}, "questions": []},
        )
        seen = {q["question"] for q in row["questions"]}
        for question in case["questions"]:
            if question["question"] not in seen:
                seen.add(question["question"])
                row["questions"].append(question)
    missing = [novel for novel in novels if novel not in merged]
    if missing:
        raise FileNotFoundError(f"missing DetectiveQA cases: {missing}")
    return merged


class BuildProgress:
    def __init__(self, root: Path, novels: list[str], cases: dict[str, dict]) -> None:
        self.root = root
        self.novels = novels
        self.cases = cases
        self.current = ""
        self.completed: list[str] = []
        self.started = time.time()
        self.stop = threading.Event()
        root.mkdir(parents=True, exist_ok=True)
        html = """<!doctype html><meta charset='utf-8'><title>Option C next 10 build</title><style>body{font:16px system-ui;max-width:900px;margin:36px auto;padding:0 18px}pre{white-space:pre-wrap;background:#f4f4f4;padding:18px;border-radius:10px}h1{font-size:24px}</style><h1>第二组 10 本小说：建图进度</h1><pre id=p>loading…</pre><script>setInterval(async()=>{try{let r=await fetch('build_progress.json?'+Date.now());let d=await r.json();p.textContent=JSON.stringify(d,null,2)}catch(e){}},1500)</script>"""
        (root / "build_progress.html").write_text(html, encoding="utf-8")
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _snapshot(self) -> dict:
        novel_dir = self.root / "novels" / self.current if self.current else None
        expected = math.ceil(max(len(self.cases[self.current]["text"]) - 100, 1) / 1400) if self.current else 0
        pass1 = len(list((novel_dir / "pass1").glob("**/chunk_*.json"))) if novel_dir else 0
        pass2 = len(list((novel_dir / "pass2").glob("**/pass2_*.json"))) if novel_dir else 0
        return {
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "phase": "complete" if len(self.completed) == len(self.novels) else "building",
            "completed_novels": len(self.completed),
            "total_novels": len(self.novels),
            "completed_ids": self.completed,
            "current_novel": self.current,
            "current_expected_chunks": expected,
            "pass1_cached_chunks": pass1,
            "pass2_cached_chunks": pass2,
            "elapsed_minutes": round((time.time() - self.started) / 60, 1),
        }

    def _loop(self) -> None:
        while not self.stop.wait(2):
            (self.root / "build_progress.json").write_text(json.dumps(self._snapshot(), ensure_ascii=False, indent=1), encoding="utf-8")

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=3)
        (self.root / "build_progress.json").write_text(json.dumps(self._snapshot(), ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novels", nargs="+", default=NOVELS)
    parser.add_argument("--graph-root", type=Path, default=ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_next10")
    parser.add_argument("--build-model", default="qwen2.5:7b-c4")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    cases = merged_cases(args.novels)
    args.graph_root.mkdir(parents=True, exist_ok=True)
    (args.graph_root / "targets.json").write_text(json.dumps(args.novels), encoding="utf-8")
    cfg = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text(encoding="utf-8"))
    cfg["model"] = {"max_tokens_pass1": 2500, "max_tokens_pass2": 4000}
    cfg["chunking"] = {"size": 1500, "overlap": 100}
    cfg["pass2_prompt"] = "v4"
    cfg["pass2_cache_variant"] = "v4_relation_centered_20260822"
    cfg["entity_consolidation"] = {"enabled": True, "cap": 360, "batch_size": 60, "anchor_count": 12}
    cfg["quality_gate"] = {"enabled": True, "max_isolate_rate": 0.60, "min_edge_node_ratio": 0.50, "max_dropped_relation_rate": 0.55}
    client = OllamaClient(args.build_model, max_tokens=4000, num_ctx=8192)
    progress = BuildProgress(args.graph_root, args.novels, cases)
    manifest = {"created": time.strftime("%Y-%m-%d %H:%M:%S"), "model": args.build_model, "chunk_size": 1500, "overlap": 100, "pass2_prompt": "v4", "novels": {}}
    try:
        for novel in args.novels:
            progress.current = novel
            graph_path = args.graph_root / "novels" / novel / "graph.json"
            if graph_path.exists():
                print(f"[{novel}] graph cached", flush=True)
            else:
                print(f"[{novel}] building graph", flush=True)
                build_case_graph(client, cases[novel], graph_path.parent, cfg, workers=args.workers, resume=True)
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            quality = dict(graph.get("quality") or {})
            if not quality.get("passed"):
                raise RuntimeError(f"[{novel}] cached graph is missing a passing quality report")
            data = graph_path.read_bytes()
            manifest["novels"][novel] = {
                "path": str(graph_path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "mtime_ns": graph_path.stat().st_mtime_ns,
                "bytes": len(data),
                "quality": quality.get("metrics") or {},
            }
            if novel not in progress.completed:
                progress.completed.append(novel)
            (args.graph_root / "build_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        manifest["completed"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (args.graph_root / "build_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        progress.close()


if __name__ == "__main__":
    main()
