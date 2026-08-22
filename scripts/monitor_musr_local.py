"""Realtime progress monitor for the MuSR local-model eval (build + 5 methods + vote)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "outputs" / "four_datasets" / "musr_local"
LOG = ROOT / "musr_local.log"
POLL = 8
STORY_PLAN = {"murder_mystery": 34, "object_placements": 8, "team_allocation": 34}
METHODS = ["basic", "v4", "v5.1", "v5.2", "v7", "vote"]
METHOD_CN = {
    "basic": "基本基线(整本阅读)",
    "v4": "v4 图谱检索",
    "v5.1": "v5.1 agentic 图推理",
    "v5.2": "v5.2 agentic+关系遍历",
    "v7": "v7 按选项抽证",
    "vote": "投票集成(v4/v5.2/v7)",
}


def gpu_status() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        parts = [p.strip() for p in out.split(",")]
        return {
            "vram_used": parts[0] if len(parts) > 0 else "?",
            "vram_total": parts[1] if len(parts) > 1 else "?",
            "util": parts[2] if len(parts) > 2 else "?",
        }
    except Exception:
        return {"vram_used": "?", "vram_total": "?", "util": "?"}


def log_stats() -> dict:
    info = {"build_ok": 0, "build_fail": 0, "build_done": 0, "answer_done": 0, "answer_total": 100}
    if not LOG.exists():
        return info
    for line in LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.search(r"\[build\] (\d+)/(\d+) .* ok", line)
        if m:
            info["build_ok"] += 1
            info["build_done"] = max(info["build_done"], int(m.group(2)))
            continue
        m = re.search(r"\[build\] (\d+)/(\d+) .* FAIL", line)
        if m:
            info["build_fail"] += 1
            info["build_done"] = max(info["build_done"], int(m.group(2)))
            continue
        m = re.search(r"\[progress\] (\d+)/(\d+) q done", line)
        if m:
            info["answer_done"] = max(info["answer_done"], int(m.group(1)))
            info["answer_total"] = max(info["answer_total"], int(m.group(2)))
    return info


def story_table() -> list[dict]:
    rows = []
    if not OUT.exists():
        return rows
    for d in sorted(OUT.glob("musr_*")):
        domain = d.name.split("_")[1]
        p1 = len(list((d / "pass1").glob("chunk_*.json"))) if (d / "pass1").exists() else 0
        p2 = len(list((d / "pass2").glob("pass2_*.json"))) if (d / "pass2").exists() else 0
        graph_ok = (d / "graph.json").exists()
        kept = (d / "pass1" / "kept.jsonl").exists()
        answers = len(list(d.glob("v7_*.json")))
        phase = "pending"
        if graph_ok and answers > 0:
            phase = "answering"
        elif graph_ok:
            phase = "graph"
        elif p2 > 0:
            phase = "pass2"
        elif p1 > 0:
            phase = "pass1"
        rows.append({"id": d.name, "domain": domain, "phase": phase, "p1": p1, "p2": p2, "graph": graph_ok, "answers": answers})
    return rows


def load_results() -> tuple[list[dict], dict]:
    path = OUT / "results.json"
    if not path.exists():
        return [], {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return [], {}
    rows = data.get("results", [])
    acc: dict[str, dict] = {}
    for r in rows:
        for m in METHODS:
            if m not in r:
                continue
            s = acc.setdefault(m, {"correct": 0, "total": 0})
            s["total"] += 1
            s["correct"] += 1 if r[m]["correct"] else 0
    return rows, acc


def render_html(payload: dict) -> str:
    rows = payload["stories"]
    graphs = sum(1 for r in rows if r["graph"])
    story_total = 76
    build_pct = graphs / story_total * 100 if story_total else 0
    answer_done = payload["answer_done"]
    answer_total = payload["answer_total"]
    answer_pct = answer_done / answer_total * 100 if answer_total else 0
    acc_rows = ""
    for m in METHODS:
        s = payload["acc"].get(m, {"correct": 0, "total": 0})
        pct = s["correct"] / s["total"] * 100 if s["total"] else 0
        acc_rows += (
            f"<tr><td>{METHOD_CN[m]}</td><td>{s['correct']}</td><td>{s['total']}</td>"
            f"<td><b>{pct:.1f}%</b></td><td><div class='bar'><i style='width:{pct:.1f}%'></i></div></td></tr>"
        )
    story_rows = ""
    for r in rows:
        phase_cn = {"pending": "排队", "pass1": "pass1", "pass2": "pass2", "graph": "建图完成", "answering": "答题中"}.get(r["phase"], r["phase"])
        story_rows += (
            f"<tr><td>{r['id']}</td><td>{r['domain'][:4]}</td><td>{phase_cn}</td>"
            f"<td>{r['p1']}</td><td>{r['p2']}</td><td>{'是' if r['graph'] else '否'}</td><td>{r['answers']}</td></tr>"
        )
    gpu = payload["gpu"]
    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="{POLL}">
<title>MuSR 本地评测 · 实时进度</title>
<style>
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:20px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:15px;margin:18px 0 8px;color:#94a3b8}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px;margin-top:12px}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:14px}}
.big{{font-size:30px;font-weight:700;color:#38bdf8}}
.sub{{font-size:12px;color:#94a3b8;margin-top:4px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}}
th,td{{padding:6px 10px;border-bottom:1px solid #334155;text-align:left}}
th{{color:#94a3b8;font-weight:600}}
.bar{{background:#334155;border-radius:4px;height:8px;width:120px;overflow:hidden}}
.bar i{{display:block;height:100%;background:#38bdf8}}
.ok{{color:#4ade80}}.bad{{color:#f87171}}
</style></head><body>
<h1>MuSR 本地评测（qwen2.5:7b）· 实时进度</h1>
<div class="sub">更新时间 {payload['updated']} · GPU {gpu['vram_used']}/{gpu['vram_total']} 显存 · 利用率 {gpu['util']} · ollama: qwen2.5:7b (16k)</div>
<div class="grid">
<div class="card"><div class="big">{graphs}/{story_total}</div><div class="sub">图谱建图（{build_pct:.0f}%）</div>
<div class="bar" style="width:100%;margin-top:8px"><i style="width:{build_pct:.1f}%"></i></div></div>
<div class="card"><div class="big">{answer_done}/{answer_total}</div><div class="sub">答题进度（{answer_pct:.0f}%）</div>
<div class="bar" style="width:100%;margin-top:8px"><i style="width:{answer_pct:.1f}%"></i></div></div>
<div class="card"><div class="big">{payload['build_fail']}</div><div class="sub">建图失败（自动跳过）</div></div>
</div>
<h2>各方法实时正确率</h2>
<table><tr><th>方法</th><th>对</th><th>总数</th><th>正确率</th><th>进度</th></tr>{acc_rows}</table>
<h2>故事级进度（76 篇）</h2>
<table><tr><th>故事</th><th>域</th><th>阶段</th><th>pass1块</th><th>pass2块</th><th>图谱</th><th>v7答案</th></tr>{story_rows}</table>
</body></html>"""


def main() -> None:
    while True:
        rows, acc = load_results()
        stats = log_stats()
        payload = {
            "updated": time.strftime("%H:%M:%S"),
            "gpu": gpu_status(),
            "stories": story_table(),
            "acc": acc,
            "build_fail": stats["build_fail"],
            "answer_done": max(stats["answer_done"], len(rows)),
            "answer_total": max(stats["answer_total"], 100),
            "results_count": len(rows),
        }
        html = render_html(payload)
        (OUT / "monitor.html").write_text(html, encoding="utf-8")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
