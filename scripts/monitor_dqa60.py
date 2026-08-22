"""Write a compact live status snapshot for the DQA60 local experiment."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "outputs" / "four_datasets" / "dqa60_single9"
STATUS = EXP / "live_status.json"
PROTOCOL = ROOT / "config" / "dqa_60_single_model_protocol.json"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def read_log_lines(path: Path) -> list[str]:
    """Read PowerShell redirected logs, which may be UTF-16 LE on Windows."""
    try:
        data = path.read_bytes()
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16", errors="replace")
        elif b"\x00" in data[:256]:
            text = data.decode("utf-16-le", errors="replace")
        else:
            text = data.decode("utf-8", errors="replace")
        return text.splitlines()[-30:]
    except Exception:
        return []


def gpu() -> dict:
    try:
        raw = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw", "--format=csv,noheader,nounits"],
            text=True,
            encoding="utf-8",
            timeout=5,
        )
        row = next(csv.reader([raw.strip()]))
        return {"name": row[0].strip(), "utilization": float(row[1]), "memory_used": float(row[2]), "memory_total": float(row[3]), "temperature": float(row[4]), "power": float(row[5])}
    except Exception as exc:
        return {"error": str(exc)}


def _write_status(snapshot: dict) -> None:
    """Atomically publish the snapshot; retry transient Windows sharing violations."""
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


def main() -> None:
    protocol = read_json(PROTOCOL, {})
    while True:
        batches = []
        active = None
        total_complete = 0
        for index in range(1, 7):
            name = f"batch{index:02d}"
            root = EXP / name
            progress = read_json(root / "build_progress.json", {})
            completed = int(progress.get("completed_novels", 0))
            total_complete += completed
            row = {"name": name, "targets": protocol.get("batches", {}).get(name, []), "exists": root.exists(), "progress": progress}
            batches.append(row)
            if progress.get("phase") == "building":
                active = row
        log_lines = []
        if active:
            path = EXP / active["name"] / "build.log"
            log_lines = read_log_lines(path)
        snapshot = {
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "experiment": "DetectiveQA 60 novels · single Qwen3.5 9B",
            "phase": "graph_build",
            "total_novels": 60,
            "graphs_completed": total_complete,
            "active_batch": active["name"] if active else None,
            "active": active,
            "batches": batches,
            "gpu": gpu(),
            "recent_log": log_lines,
            "methods": {"graph": 5, "baselines": 3},
            "external_api": False,
            "model": "qwen3.5:9b",
        }
        try:
            _write_status(snapshot)
        except Exception as exc:
            print(f"[monitor] {time.strftime('%H:%M:%S')} write failed: {exc!r}", file=sys.stderr, flush=True)
        time.sleep(2)


if __name__ == "__main__":
    main()
