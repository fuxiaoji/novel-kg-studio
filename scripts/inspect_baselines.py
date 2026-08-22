"""Print baseline results with answer-in-context audit."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def main() -> None:
    rows = json.loads((OUT / "baselines_results.json").read_text(encoding="utf-8"))
    for row in rows:
        ft = row.get("full_text") or {}
        ai = row.get("answer_in_context") or {}
        print(f"{row['question'][:42]} mask={row['mask']:.2f} | full_text judged={ft.get('judged')} | {str(ft.get('answer', ''))[:90]}")
        print(
            f"   答案段在上下文: masked={ai.get('masked_text')} chunk={ai.get('chunk_bm25')} "
            f"sentence={ai.get('sentence_bm25')} full_text={ai.get('full_text')}"
        )


if __name__ == "__main__":
    main()
