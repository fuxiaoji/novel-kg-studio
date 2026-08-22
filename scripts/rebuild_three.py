"""Rebuild the three sparse graphs (106/108/121) and regenerate their answers.

These novels' pass1 caches were produced during the broken-model window (kept=[]),
so their graphs have almost no nodes. The stale graph artifacts are moved to
backup_sparse_<date>/ beforehand by the launcher; this script only rebuilds.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import os  # noqa: E402

os.chdir(ROOT)

OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"
NOVELS = ["106", "108", "121"]
VARIANTS = ["v4", "v5a", "v5b", "v7"]
ANSWER_MODEL = "qwen2.5:7b-32k"
BUILD_MODEL = "qwen2.5:7b-c4"

from detectiveqa_three_groups import main as three_main  # noqa: E402
from goldonly_baseline import main as goldonly_main  # noqa: E402


def run_three(novel: str, retrieval: str, masked: bool) -> None:
    argv = [
        "detectiveqa_three_groups.py",
        "--novels", novel,
        "--workers", "8",
        "--answer-workers", "3",
        "--groups", "graph", "tail", "compress",
        "--retrieval", retrieval,
        "--answer-model", ANSWER_MODEL,
        "--build-model", BUILD_MODEL,
    ]
    if not masked:
        argv.append("--no-mask-graph")
    argv += ["--out", str(OUT / "results_rebuild_three.json")]
    sys.argv = argv
    three_main()


def run_gold(novel: str) -> None:
    sys.argv = [
        "goldonly_baseline.py",
        "--backend", "qwen",
        "--variants", "masked", "unmasked",
        "--novels", novel,
        "--model", ANSWER_MODEL,
        "--out", str(OUT / "results_goldonly_30novels.json"),
    ]
    goldonly_main()


if __name__ == "__main__":
    for nid in NOVELS:
        print(f"=== REBUILD novel {nid} ===", flush=True)
        for variant in VARIANTS:
            for masked in (True, False):
                tag = "masked" if masked else "unmasked"
                print(f"=== novel {nid}: {variant} {tag} ===", flush=True)
                run_three(nid, variant, masked)
        run_gold(nid)
        (OUT / "novels" / nid / "done_30novels.txt").write_text("done", encoding="utf-8")
        print(f"=== novel {nid}: REBUILD DONE ===", flush=True)
    print("rebuild_three all done", flush=True)
