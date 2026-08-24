"""Wait for the old20 missing-baselines run to finish, then execute the paper pipeline.

Chain: pure-graph aggregation -> figures -> archive -> final report -> manuscript
finalize -> tectonic compile -> deliverables integrity.  Logs every step to
paper/build/pipeline_run.log and stops on first failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_OUT = ROOT / "outputs" / "four_datasets" / "dqa30_frozen_old20_baselines9b"
PROGRESS = BASE_OUT / "progress.json"
RESUME_PID = BASE_OUT / "resume.pid"
PY = r"C:\Users\fwj\AppData\Local\Programs\Python\Python313\python.exe"
TECTONIC = r"C:\Users\fwj\.codex\.tmp\bundled-marketplaces\openai-bundled\plugins\latex\bin\tectonic.exe"
LOG = ROOT / "paper" / "build" / "pipeline_run.log"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')}  {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def run(cmd: list[str], cwd: Path, *, check: bool = True) -> int:
    log(f"> {' '.join(cmd)}")
    env = dict(__import__("os").environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if result.stdout.strip():
        log("  stdout: " + result.stdout.strip()[-500:])
    if result.stderr.strip():
        log("  stderr: " + result.stderr.strip()[-800:])
    if check and result.returncode != 0:
        raise RuntimeError(f"step failed with rc={result.returncode}: {' '.join(cmd)}")
    return result.returncode


def baselines_finished() -> bool:
    if not PROGRESS.exists():
        return False
    try:
        progress = json.loads(PROGRESS.read_text(encoding="utf-8"))
    except Exception:
        return False
    return int(progress.get("completed_questions") or 0) >= int(progress.get("total_questions") or 0)


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log("=== pipeline watcher started ===")
    # 1. wait for the baseline runner to finish
    while not baselines_finished():
        time.sleep(60)
        log("waiting for baselines ...")
    log("baselines complete")

    # 2. pure-graph aggregation (writes frozen_results.json / csv / results_macros / main_results_table)
    run([PY, "scripts/aggregate_dqa30_pure_graph_results.py"], ROOT)

    # 3. figures
    run([PY, "scripts/plot_dqa30_pairwise.py"], ROOT)
    run([PY, "scripts/analyze_dqa30_effect_relationships.py"], ROOT)
    run([PY, "scripts/plot_dqa30_paper_figures.py", "--manifest",
         "paper/generated/novel103_plot_manifest.json"], ROOT)

    # 4. archive answer records + final report
    run([PY, "scripts/archive_dqa30_answer_records.py"], ROOT)
    run([PY, "scripts/write_dqa30_final_report.py"], ROOT)

    # 5. finalize manuscript source (idempotent) then compile
    run([PY, "scripts/finalize_manuscript_source.py"], ROOT)
    run([TECTONIC, "-X", "compile", "paper/manuscript.tex", "--outdir", "paper/build"], ROOT)

    # 6. deliverables integrity
    run([PY, "scripts/finalize_dqa30_deliverables.py"], ROOT)

    log("=== pipeline completed ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"PIPELINE FAILED: {type(exc).__name__}: {exc}")
        sys.exit(1)
