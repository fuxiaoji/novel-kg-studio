"""Persistent closed-loop pipeline: build ten new graphs, then evaluate eight paper methods."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOVELS = ["93", "97", "99", "100", "103", "104", "105", "106", "107", "108"]


def write_status(path: Path, phase: str, state: str, **extra: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "phase": phase,
                "state": state,
                **extra,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def run_phase(status_path: Path, phase: str, command: list[str]) -> None:
    write_status(status_path, phase, "running", command=command)
    print(f"[{time.strftime('%F %T')}] START {phase}: {' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        write_status(status_path, phase, "failed", returncode=completed.returncode)
        raise SystemExit(completed.returncode)
    write_status(status_path, phase, "complete")
    print(f"[{time.strftime('%F %T')}] COMPLETE {phase}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT / "outputs" / "four_datasets" / "dqa30_attention",
    )
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--build-workers", type=int, default=2)
    parser.add_argument("--summary-workers", type=int, default=1)
    args = parser.parse_args()

    graph_root = args.root / "batch03"
    eval_root = args.root / "batch03_eval"
    status_path = args.root / "pipeline_status.json"
    python = sys.executable
    run_phase(
        status_path,
        "build_10",
        [
            python,
            "-u",
            str(ROOT / "scripts" / "build_c_next10_graphs.py"),
            "--novels",
            *NOVELS,
            "--graph-root",
            str(graph_root),
            "--build-model",
            args.model,
            "--workers",
            str(args.build_workers),
        ],
    )
    run_phase(
        status_path,
        "evaluate_10",
        [
            python,
            "-u",
            str(ROOT / "scripts" / "run_dqa30_batch_eval.py"),
            "--novels",
            *NOVELS,
            "--graph-root",
            str(graph_root),
            "--out-root",
            str(eval_root),
            "--model",
            args.model,
            "--summary-workers",
            str(args.summary_workers),
        ],
    )
    write_status(
        status_path,
        "pipeline",
        "complete",
        graph_root=str(graph_root),
        eval_root=str(eval_root),
        novels=NOVELS,
    )


if __name__ == "__main__":
    main()
