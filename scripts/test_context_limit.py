"""Measure the real context limit of the DeepSeek API with the novel-sized inputs."""

from __future__ import annotations

import json
import os
from pathlib import Path

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"
NOVEL = ROOT.parent / "kg-detect" / "datasets" / "external" / "detectiveqa" / "novel_103.txt"


def main() -> None:
    kept = [
        json.loads(line)
        for line in (OUT / "pass1" / "kept.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kept_text = "\n".join(row["text"] for row in kept)
    novel_text = NOVEL.read_text(encoding="utf-8")
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    for name, text in (("kept_text", kept_text), ("raw_novel", novel_text)):
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": f"Say OK.\n\n{text[: len(text)]}"}],
                max_tokens=8,
            )
            usage = response.usage
            print(f"{name}: OK chars={len(text)} prompt_tokens={usage.prompt_tokens if usage else '?'}")
        except Exception as exc:
            print(f"{name}: ERROR chars={len(text)} -> {str(exc)[:220]}")


if __name__ == "__main__":
    main()
