"""Print masked-text vs RAG answers for manual judgment."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def main() -> None:
    rows = json.loads((OUT / "masked_qa_results.json").read_text(encoding="utf-8"))
    for row in rows:
        print("=" * 70)
        print(f"Q: {row['question']}  (mask={row['mask']:.2f}, 金标: {row['gold_answer']})")
        print(f"  文本基线: {row['masked_text_answer'][:240]}")
        print(f"  RAG管线 : {row['rag_answer'][:240]}")


if __name__ == "__main__":
    main()
