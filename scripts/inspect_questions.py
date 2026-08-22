"""Print the question-set results (answers, orders, masking effect)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "demo"


def main() -> None:
    rows = json.loads((OUT / "question_set_results.json").read_text(encoding="utf-8"))
    for row in rows:
        print("=" * 72)
        print(f"Q: {row['question']}  (mask={row['mask']:.2f})")
        print(f"理解: {row['interpretation'][:200]}")
        print(f"扩展: {row['expanded_query']}")
        print(f"一阶: {', '.join(row['first_order'][:8])}")
        print(f"二阶: {', '.join(row['second_order'][:8])}")
        print(f"三阶(前8): {', '.join(row['third_order'][:8])}")
        print(f"覆盖 2阶/3阶: {row['gold_coverage']['first_second']} / {row['gold_coverage']['first_second_third']} | 三阶信息型占比: {row['third_order_informative_ratio']}")
        if row.get("verifications"):
            print("证词验证:")
            for v in row["verifications"]:
                print(f"   {v['candidate']}: {v['verdict']} ({v['confidence']}) {v['reason'][:90]}")
        print(f"回答: {row['answer'][:300]}")


if __name__ == "__main__":
    main()
