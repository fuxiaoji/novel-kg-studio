"""Persistent build -> answer -> analyze pipeline for the second ten novels."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "outputs" / "four_datasets" / "dqa_qwen_c_next10" / "pipeline_status.json"


def status(phase: str, state: str, **extra: object) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "phase": phase, "state": state, **extra}, ensure_ascii=False, indent=1), encoding="utf-8")


def run(phase: str, script: str, *args: str) -> None:
    status(phase, "running")
    command = [sys.executable, str(ROOT / "scripts" / script), *args]
    print(f"[{time.strftime('%F %T')}] START {phase}: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        status(phase, "failed", returncode=completed.returncode)
        raise SystemExit(completed.returncode)
    status(phase, "complete")
    print(f"[{time.strftime('%F %T')}] COMPLETE {phase}", flush=True)


def main() -> None:
    run("build", "build_c_next10_graphs.py", "--workers", "8")
    run("answers", "run_c_next10_methods.py", "--answer-workers", "3")
    run("analysis", "analyze_c_next10_methods.py")
    status("pipeline", "complete")


if __name__ == "__main__":
    main()
