"""Realtime progress monitor for the 30-novel DetectQA run.

Tracks per novel:
  pass1 / pass2 / graph build, 8 graph-variant answer rounds (v4/v5a/v5b/v7 x masked/unmasked),
  tail + compress baselines, gold-only (masked/unmasked), and overall done marker.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from eval_four_datasets import load_cases  # noqa: E402
from goldonly_baseline import load_anno  # noqa: E402

OUT = (
    Path(sys.argv[sys.argv.index("--out-root") + 1])
    if "--out-root" in sys.argv
    else ROOT / "outputs" / "four_datasets" / "dqa_qwen"
)
NOVEL_DIR = OUT / "novels"
STATE = OUT / "monitor_state.json"
def _cli_arg(name: str, default: str) -> str:
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default

CHUNK_SIZE = int(_cli_arg("--chunk-size", "4000"))
CHUNK_OVERLAP = int(_cli_arg("--chunk-overlap", "200"))
POLL = 10
ANSWER_MODEL = "qwen2.5:7b-32k"
MTAG = f"|{ANSWER_MODEL}"
PROMPT_VER = "v1"
VARIANTS = {
    "v4_m": ("graph_", "r4"),
    "v4_u": ("graphnm_", "r4"),
    "v5a_m": ("graph_", "r5a"),
    "v5a_u": ("graphnm_", "r5a"),
    "v5b_m": ("graph_", "r5b"),
    "v5b_u": ("graphnm_", "r5b"),
    "v7_m": ("graph_", "r7"),
    "v7_u": ("graphnm_", "r7"),
}


def chunk_total(text_len: int, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> int:
    if text_len <= size:
        return 1
    step = size - overlap
    return (text_len - size + step - 1) // step + 1


def count_files(directory: Path, pattern: str) -> int:
    if not directory.exists():
        return 0
    return len(list(directory.glob(pattern)))


def gkey(nid: str, qi: int, salt: str) -> str:
    return hashlib.sha1(f"{nid}|{qi}|{PROMPT_VER}|{salt}{MTAG}".encode("utf-8")).hexdigest()[:10]


def key_base(nid: str, qi: int) -> str:
    return hashlib.sha1(f"{nid}|{qi}|{PROMPT_VER}{MTAG}".encode("utf-8")).hexdigest()[:10]


def gpu_status() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        parts = [p.strip() for p in out.split(",")]
        gpu = {"vram_used": parts[0] if len(parts) > 0 else "?", "vram_total": parts[1] if len(parts) > 1 else "?", "util": parts[2] if len(parts) > 2 else "?"}
    except Exception:
        gpu = {"vram_used": "?", "vram_total": "?", "util": "?"}
    try:
        ps = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10).stdout
        rows = [l for l in ps.splitlines()[1:] if l.strip()]
        gpu["models"] = [r.split() for r in rows[:4]]
    except Exception:
        gpu["models"] = []
    return gpu


def main() -> None:
    targets_path = OUT / "large15_targets.json"
    if not targets_path.exists():
        targets_path = OUT / "targets.json"
    targets = json.loads(targets_path.read_text(encoding="utf-8")) if targets_path.exists() else None
    cases = [c for c in load_cases("detectiveqa")]
    by_novel: dict[str, dict] = {}
    for c in cases:
        nid = str(c["meta"]["novel_id"])
        merged = by_novel.setdefault(nid, {"text_len": 0, "questions": []})
        merged["text_len"] = max(merged["text_len"], len(c["text"]))
        for q in c["questions"]:
            if q["question"] not in {x["question"] for x in merged["questions"]}:
                merged["questions"].append(q)
    prev: dict = {}
    if STATE.exists():
        prev = json.loads(STATE.read_text(encoding="utf-8"))
    order = targets if targets else sorted(by_novel, key=int)
    while True:
        try:
            novels = []
            answered_total = 0
            answer_total_all = 0
            for nid in order:
                d = NOVEL_DIR / nid
                text_len = by_novel.get(nid, {}).get("text_len", 0)
                questions = by_novel.get(nid, {}).get("questions", [])
                q_total = len(questions)
                p1_total = chunk_total(text_len)
                p1_done = count_files(d / "pass1", f"s{CHUNK_SIZE}_o{CHUNK_OVERLAP}/chunk_*.json")
                p2_dirs = list((d / "pass2").glob("s*/pass2_*.json")) if (d / "pass2").exists() else []
                p2_done = len(p2_dirs)
                graph_ok = (d / "graph.json").exists()
                variants: dict[str, dict] = {}
                for vname, (prefix, salt) in VARIANTS.items():
                    done = sum(1 for qi in range(q_total) if (d / f"{prefix}{gkey(nid, qi, salt)}.json").exists())
                    variants[vname] = {"done": done, "total": q_total}
                    answered_total += done
                    answer_total_all += q_total
                for vname, prefix in [("ens_m", "ens_m_"), ("ens_u", "ens_u_")]:
                    done = sum(1 for qi in range(q_total) if (d / f"{prefix}{key_base(nid, qi)}.json").exists())
                    variants[vname] = {"done": done, "total": q_total}
                    answered_total += done
                    answer_total_all += q_total
                tail_done = sum(1 for qi in range(q_total) if (d / f"tail_{key_base(nid, qi)}.json").exists())
                comp_done = sum(1 for qi in range(q_total) if (d / f"comp_{key_base(nid, qi)}.json").exists())
                compress_done = count_files(d / "compress_cache", "chunk_*.json") + count_files(d / "compress_cache", "l2_*.json")
                gold_total = 0
                gold_done = 0
                for anno in load_anno(nid):
                    gold_total += 2
                    for variant in ("masked", "unmasked"):
                        k = hashlib.sha1(f"{nid}|{anno['source']}|{anno['qi']}|{variant}|v1|{ANSWER_MODEL}".encode("utf-8")).hexdigest()[:10]
                        if (OUT / "goldonly" / nid / f"{variant}_{k}.json").exists():
                            gold_done += 1
                done_marker = (d / "done_30novels.txt").exists() or (d / "done_10novels_c.txt").exists()
                phase = "pending"
                if done_marker:
                    phase = "done"
                elif graph_ok:
                    phase = "answering"
                elif p2_done > 0:
                    phase = "pass2"
                elif p1_done > 0:
                    phase = "pass1"
                novels.append(
                    {
                        "id": nid,
                        "phase": phase,
                        "pass1": {"done": p1_done, "total": p1_total},
                        "pass2": {"done": p2_done, "total": None},
                        "graph_built": graph_ok,
                        "variants": variants,
                        "tail": {"done": tail_done, "total": q_total},
                        "compress": {"done": compress_done},
                        "gold": {"done": gold_done, "total": gold_total},
                        "novel_done": done_marker,
                    }
                )
            # ETA from answered-rate (answered files per minute)
            now = time.time()
            history = prev.get("history", [])
            history.append([now, answered_total])
            history = [h for h in history if now - h[0] < 900]
            eta = None
            if len(history) >= 2:
                dt = history[-1][0] - history[0][0]
                ddone = history[-1][1] - history[0][1]
                if dt > 60 and ddone > 0:
                    rate = ddone * 60.0 / dt
                    eta = (answer_total_all - answered_total) / rate if rate > 0 else None
            payload = {
                "updated": time.strftime("%H:%M:%S"),
                "novels": novels,
                "gpu": gpu_status(),
                "eta_min": round(eta, 1) if eta else None,
            }
            live_path = OUT / "live_accuracy.json"
            if live_path.exists():
                try:
                    payload["live"] = json.loads(live_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            (OUT / "monitor.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            (OUT / "monitor.js").write_text(
                "window.MONITOR_DATA = " + json.dumps(payload, ensure_ascii=False) + ";",
                encoding="utf-8",
            )
            STATE.write_text(json.dumps({"history": history}, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[monitor] iteration error: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
