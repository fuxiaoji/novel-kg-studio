"""Fairer graph-vs-compression attribution: paragraph masking plus graph links."""

from __future__ import annotations

import re

import run_dqa_attention_proxy as batched_entry  # noqa: F401
import run_dqa_attention_proxy_pilot as pilot


pilot.OUT = pilot.BASE / "attention_proxy_v2"


def _graph_context(item: dict, gold_positions: list[int]):
    chunks = item["retrieval"]["chunks"]
    links = item["retrieval"]["links"]
    gold_ids = {f"[{position}]" for position in gold_positions if position >= 0}
    hit_ids = [row["id"] for row in chunks if any(marker in row["text"] for marker in gold_ids)]
    hit_set = set(hit_ids)

    def passages(mask_gold: bool):
        rows = []
        for chunk in chunks:
            text = chunk["text"]
            if mask_gold and chunk["id"] in hit_set:
                for position in gold_positions:
                    pattern = re.compile(rf"(?ms)^\[{position}\]\s*.*?(?=^\[\d+\]\s*|\Z)")
                    text = pattern.sub(f"[{position}] [GOLD PARAGRAPH REMOVED]\n", text)
            rows.append(f"[{chunk['id']}]\n{text}")
        return "\n\n".join(rows)

    def relation_rows(mask_gold: bool):
        visible = [row for row in links if not (mask_gold and row["chunk_id"] in hit_set)]
        return "\n".join(
            f"{row['candidate']}: {row['source']} --{row['relation']}--> {row['target']} "
            f"[{row['chunk_id']}: {row['evidence'][:240]}]"
            for row in visible
        ) or "[no grounded graph relation remains]"

    full = passages(False) + "\n\nGROUNDED GRAPH RELATIONS\n" + relation_rows(False)
    ablated = passages(True) + "\n\nGROUNDED GRAPH RELATIONS\n" + relation_rows(True)
    return full, ablated, hit_ids


pilot.graph_context = _graph_context


if __name__ == "__main__":
    pilot.main()
