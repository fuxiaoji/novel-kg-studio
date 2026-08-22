"""Orchestrate the 15-novel large validation with Qwen (graph v4 masked/unmasked, tail, compress, gold-only)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import os  # noqa: E402

os.chdir(ROOT)

NOVELS = ["103", "104", "117", "100", "106", "108", "121", "124", "127", "133", "140", "142", "145", "198", "209"]
OUT = ROOT / "outputs" / "four_datasets" / "dqa_qwen"
(OUT / "large15_targets.json").write_text(__import__("json").dumps(NOVELS), encoding="utf-8")

from detectiveqa_three_groups import main as three_main  # noqa: E402
from goldonly_baseline import main as goldonly_main  # noqa: E402


def run_three(*extra: str) -> None:
    sys.argv = [
        "detectiveqa_three_groups.py",
        "--novels", *CURRENT_NOVELS,
        "--workers", "8",
        "--groups", "graph", "tail", "compress",
        *extra,
        "--out", str(OUT / "results_large15.json"),
    ]
    three_main()


if __name__ == "__main__":
    # interleave masked + unmasked per novel so both update together
    for nid in NOVELS:
        CURRENT_NOVELS = [nid]
        print(f"=== novel {nid}: masked pass ===", flush=True)
        run_three("--retrieval", "v4")
        print(f"=== novel {nid}: unmasked pass ===", flush=True)
        run_three("--retrieval", "v4", "--no-mask-graph")
    # restore full target list for the monitor
    (OUT / "targets.json").write_text(json.dumps(NOVELS), encoding="utf-8")
    # gold-only Qwen masked + unmasked
    sys.argv = [
        "goldonly_baseline.py",
        "--backend", "qwen",
        "--variants", "masked", "unmasked",
        "--novels", *NOVELS,
        "--out", str(OUT / "results_goldonly_large15.json"),
    ]
    goldonly_main()
    print("large15 all done", flush=True)
