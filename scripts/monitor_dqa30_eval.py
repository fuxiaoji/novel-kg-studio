"""Live status snapshot for the DQA30 batch eval (answering) phase.

Reads progress.json (written by run_dqa30_batch_eval.py after every question)
plus the per-question answer files, and publishes a compact live_status.json
for the dashboard. Mirrors monitor_dqa60.py's atomic-write pattern.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "outputs" / "four_datasets" / "dqa30_attention"
EVAL = EXP / "batch03_eval"
STATUS = EVAL / "live_status.json"
PROGRESS = EVAL / "progress.json"
ANSWERS = EVAL / "answers"
METHODS = ("G1", "G2", "G3", "G4", "G5", "B1", "B2", "B3", "Q0")
GROUP = {"graph": ("G1", "G2", "G3", "G4", "G5"), "baseline": ("B1", "B2", "B3"), "probe": ("Q0",)}


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_status(snapshot: dict) -> None:
    temp = STATUS.with_suffix(".tmp")
    payload = json.dumps(snapshot, ensure_ascii=False, indent=1)
    for _ in range(5):
        try:
            temp.write_text(payload, encoding="utf-8")
            temp.replace(STATUS)
            return
        except PermissionError:
            time.sleep(0.3)
    raise RuntimeError("could not replace live_status.json after retries")


def scan_answers() -> tuple[dict[str, dict], dict[str, int], list[str]]:
    """Return (method_stats, per_novel_answered, recent_lines)."""
    stats = {method: {"answered": 0, "correct": 0} for method in METHODS}
    novels: dict[str, int] = {}
    files: list[Path] = []
    if ANSWERS.exists():
        files = sorted(ANSWERS.glob("*/q*.json"), key=lambda p: p.stat().st_mtime)
    recent: list[str] = []
    for path in files:
        row = read_json(path, None)
        if not row or not isinstance(row, dict):
            continue
        novel = str(row.get("novel", path.parent.name))
        novels[novel] = novels.get(novel, 0) + 1
        correct = row.get("correct") or {}
        letters = {
            method: ((row.get("answers") or {}).get(method) or {}).get("selected_letter")
            for method in METHODS
        }
        qi = row.get("qi")
        qid = row.get("qid", "")
        flag = ""
        if isinstance(correct, dict):
            for method in METHODS:
                stats[method]["answered"] += 1
                if correct.get(method):
                    stats[method]["correct"] += 1
            # which methods got it right, as letters line
            flag = "✓" if all(correct.get(m) for m in METHODS) else ""
        letter_line = " ".join(f"{m}={letters[m] or '-'}" for m in METHODS)
        recent.append(f"[{novel}/q{qi:02d} {qid}] {letter_line} {flag}".rstrip())
    return stats, novels, recent[-12:]


def main() -> None:
    last_error = ""
    while True:
        snapshot: dict = {}
        try:
            progress = read_json(PROGRESS, {})
            stats, novels, recent = scan_answers()
            completed = int(progress.get("completed_questions") or 0)
            total = int(progress.get("total_questions") or 0)
            fresh = abs(time.time() - PROGRESS.stat().st_mtime) < 600 if PROGRESS.exists() else False
            per_hour = float(progress.get("per_hour") or 0.0)
            eta_minutes = float(progress.get("eta_minutes") or 0.0)
            pct = round(completed / total * 100, 1) if total else 0.0
            methods = {
                method: {
                    "answered": stats[method]["answered"],
                    "correct": stats[method]["correct"],
                    "accuracy": round(stats[method]["correct"] / stats[method]["answered"], 4)
                    if stats[method]["answered"]
                    else None,
                }
                for method in METHODS
            }
            health = "running" if fresh else "stale"
            snapshot = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "experiment": "DQA30 attention · batch03 答题 · qwen3.5:9b",
                "progress": {
                    "completed": completed,
                    "total": total,
                    "percent": pct,
                    "current": progress.get("current", ""),
                    "per_hour": round(per_hour, 1),
                    "eta_minutes": round(eta_minutes, 1),
                },
                "methods": methods,
                "method_order": list(METHODS),
                "groups": GROUP,
                "novels": {novel: {"answered": count} for novel, count in sorted(novels.items())},
                "recent": recent,
                "health": health,
            }
            last_error = ""
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            snapshot = {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "health": "error",
                "last_error": last_error,
                "progress": {"completed": 0, "total": 0, "percent": 0.0, "current": "", "per_hour": 0.0, "eta_minutes": 0.0},
            }
        try:
            _write_status(snapshot)
        except Exception as exc:
            print(f"[monitor] {time.strftime('%H:%M:%S')} write failed: {exc!r}", flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
