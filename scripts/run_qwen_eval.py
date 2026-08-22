"""Launcher for the local Qwen2.5-7B evaluation (avoids non-ASCII CLI args)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
os.chdir(ROOT)

QWEN_PATH = r"E:\desktop\coding\科研\models\Qwen2.5-7B-Instruct-GGUF\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"

sys.argv = [
    "eval_four_datasets.py",
    "--datasets", "musr", "detectbench", "turnabout", "detectiveqa",
    "--sample", "3",
    "--detectiveqa-novels", "103", "104", "117",
    "--backend", "qwen",
    "--model", QWEN_PATH,
    "--skip-graph",
    "--max-chars", "24000",
    "--out", "outputs/four_datasets/eval_results_qwen.json",
]

from eval_four_datasets import main as eval_main  # noqa: E402

eval_main()
