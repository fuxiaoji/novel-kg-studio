"""Launcher for the Ollama GPU evaluation (Qwen2.5-7B via local OpenAI-compatible server)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import os  # noqa: E402

os.chdir(ROOT)

sys.argv = [
    "eval_four_datasets.py",
    "--datasets", "musr", "detectbench", "turnabout", "detectiveqa",
    "--sample", "3",
    "--detectiveqa-novels", "103", "104", "117",
    "--backend", "ollama",
    "--model", "qwen2.5:7b",
    "--skip-graph",
    "--max-chars", "24000",
    "--mask-detailed",
    "--out", "outputs/four_datasets/eval_results_ollama.json",
]

from eval_four_datasets import main as eval_main  # noqa: E402

eval_main()
