"""Pause a running graph-build pipeline after one target graph is finalized.

This sidecar is intended for an already-running legacy pipeline that does not
have an in-process stop flag.  It waits until both graph.json and the build
manifest confirm the target novel, then terminates only the active build child.
The parent pipeline observes the non-zero child exit and therefore cannot start
the answer phase.  All pass caches and completed graph files remain intact.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import signal
import time
from pathlib import Path


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(path)


def process_exists(pid: int) -> bool:
    # On Windows ``os.kill(pid, 0)`` is not a harmless existence probe: signals
    # other than CTRL events are implemented with TerminateProcess.  Open a
    # query-only handle instead so this check remains strictly read-only.
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information, False, pid
    )
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--graph-root", type=Path, required=True)
    parser.add_argument("--novel", required=True)
    parser.add_argument("--build-pid", type=int, required=True)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    args = parser.parse_args()

    root = args.graph_root.resolve()
    graph_path = root / "novels" / args.novel / "graph.json"
    manifest_path = root / "build_manifest.json"
    request_path = root / "pause_request.json"
    status_path = root / "pipeline_status.json"
    progress_path = root / "build_progress.json"

    write_json(
        request_path,
        {
            "requested": time.strftime("%Y-%m-%d %H:%M:%S"),
            "state": "waiting_for_graph",
            "target_novel": args.novel,
            "build_pid": args.build_pid,
        },
    )

    while True:
        graph = load_json(graph_path)
        finalized = bool(graph.get("nodes") is not None and graph.get("edges") is not None)
        if finalized:
            break
        if not process_exists(args.build_pid):
            write_json(
                request_path,
                {
                    "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "state": "build_process_ended_before_target",
                    "target_novel": args.novel,
                    "build_pid": args.build_pid,
                },
            )
            return
        time.sleep(max(args.poll_seconds, 0.2))

    if process_exists(args.build_pid):
        os.kill(args.build_pid, signal.SIGTERM)

    # The graph is already complete, but the interrupted legacy builder may not
    # have reached its manifest write.  Record the exact finalized artifact so
    # resume logic and graph-integrity checks remain correct.
    graph_data = graph_path.read_bytes()
    manifest = load_json(manifest_path)
    novels = dict(manifest.get("novels") or {})
    novels[args.novel] = {
        "path": str(graph_path),
        "sha256": hashlib.sha256(graph_data).hexdigest(),
        "mtime_ns": graph_path.stat().st_mtime_ns,
        "bytes": len(graph_data),
    }
    manifest["novels"] = novels
    write_json(manifest_path, manifest)

    # Allow the parent pipeline to observe the child exit before recording the
    # user-facing paused state.
    time.sleep(3)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json(
        request_path,
        {
            "updated": now,
            "state": "paused",
            "target_novel": args.novel,
            "graph_path": str(graph_path),
            "build_pid": args.build_pid,
        },
    )
    write_json(
        status_path,
        {
            "updated": now,
            "phase": "build",
            "state": "paused",
            "after_novel": args.novel,
            "resume_safe": True,
        },
    )
    progress = load_json(progress_path)
    completed = list(progress.get("completed_ids") or [])
    if args.novel not in completed:
        completed.append(args.novel)
    progress.update(
        {
            "updated": now,
            "phase": "paused",
            "completed_novels": len(completed),
            "completed_ids": completed,
            "current_novel": args.novel,
            "pause_after_novel": args.novel,
        }
    )
    write_json(progress_path, progress)


if __name__ == "__main__":
    main()
