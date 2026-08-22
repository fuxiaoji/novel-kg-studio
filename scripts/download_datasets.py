"""Download the four benchmark datasets (MuSR, DetectBench, DetectiveQA, TurnaboutLLM).

Sources:
  MuSR            HF TAUR-Lab/MuSR            (via hf-mirror)
  DetectBench     GitHub MikeGu721/DetectBench (raw)
  DetectiveQA     HF Phospheneser/DetectiveQA  (via hf-mirror, English side)
  TurnaboutLLM    GitHub zharry29/turnabout_llm (raw data + eval reports)
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT.parent / "datasets" / "external"

HF = "https://hf-mirror.com"
RAW = "https://raw.githubusercontent.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _urlopen(url: str, timeout: int = 60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)


def _download(url: str, dest: Path, *, label: str) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"[skip] {label}: {dest.name}")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            with _urlopen(url) as resp, open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
            print(f"[ok] {label}: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
            return True
        except Exception as exc:
            last_err = exc
            time.sleep(2.0 * (attempt + 1))
    print(f"[FAIL] {label}: {url} -> {last_err!r}")
    return False


def _hf_files(repo: str) -> list[str]:
    url = f"{HF}/api/datasets/{repo}"
    with _urlopen(url) as resp:
        info = json.loads(resp.read().decode("utf-8"))
    return [s["rfilename"] for s in info["siblings"]]


def download_musr() -> None:
    dest_dir = OUT_ROOT / "musr"
    for name in ["all.csv", "murder_mystery.csv", "object_placements.csv", "team_allocation.csv"]:
        url = f"{HF}/datasets/TAUR-Lab/MuSR/resolve/main/{name}"
        _download(url, dest_dir / name, label="musr")


def download_detectbench() -> None:
    dest_dir = OUT_ROOT / "detectbench"
    for name in ["train.jsonl", "dev.jsonl", "test.jsonl", "test-hard.jsonl", "test-distract.jsonl"]:
        url = f"{RAW}/MikeGu721/DetectBench/main/DetectBench_eng_v1.3/{name}"
        _download(url, dest_dir / name, label="detectbench")


def download_detectiveqa() -> None:
    dest_dir = OUT_ROOT / "detectiveqa"
    files = [f for f in _hf_files("Phospheneser/DetectiveQA") if f.startswith(("anno_data_en/", "novel_data_en/"))]
    print(f"detectiveqa: {len(files)} english files to fetch")
    for rel in files:
        quoted = "/".join(urllib.parse.quote(p) for p in rel.split("/"))
        url = f"{HF}/datasets/Phospheneser/DetectiveQA/resolve/main/{quoted}"
        _download(url, dest_dir / rel, label="detectiveqa")


def download_turnabout() -> None:
    dest_dir = OUT_ROOT / "turnabout_llm"
    for rel in [
        "data/AA_integrated_dataset.json",
        "data/DR_integrate_dataset.json",
        "data/README.md",
        "README.md",
    ]:
        url = f"{RAW}/zharry29/turnabout_llm/main/{rel}"
        _download(url, dest_dir / rel, label="turnabout")
    # baseline reports from the official eval folder
    for rel in [
        "eval/deepseek-chat_prompt_base_report.json",
        "eval/deepseek-reasoner_prompt_base_report.json",
        "eval/gpt-4.1-mini_prompt_base_report.json",
        "eval/gpt-4.1_prompt_base_report.json",
        "eval/llama-3.1-8b_prompt_base_report.json",
        "eval/llama-3.1-8b_prompt_cot_one_shot_report.json",
        "eval/llama-3.1-70b_prompt_base_report.json",
        "eval/o3-mini_prompt_base_report.json",
        "eval/o4-mini_prompt_base_report.json",
        "eval/QwQ-32B_prompt_base_report.json",
        "eval/deepseek-R1-8b_prompt_base_report.json",
        "eval/eval.csv",
    ]:
        url = f"{RAW}/zharry29/turnabout_llm/main/{rel}"
        _download(url, dest_dir / rel, label="turnabout-eval")


def main() -> None:
    which = sys.argv[1:] or ["musr", "detectbench", "detectiveqa", "turnabout"]
    print("output root:", OUT_ROOT)
    if "musr" in which:
        download_musr()
    if "detectbench" in which:
        download_detectbench()
    if "detectiveqa" in which:
        download_detectiveqa()
    if "turnabout" in which:
        download_turnabout()
    print("done")


if __name__ == "__main__":
    main()
