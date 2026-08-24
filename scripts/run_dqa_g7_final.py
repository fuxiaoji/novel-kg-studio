"""Final G7 entry point with deterministic parsing of explicit option mappings."""

from __future__ import annotations

import json
import re

import run_dqa_g7_robust as robust_entry  # noqa: F401
import run_dqa_g7_pure_graph as g7


_complete_choice = g7.complete_choice


def _final_choice(client, system: str, user: str, max_tokens: int = 320):
    parsed = _complete_choice(client, system, user, max_tokens=max_tokens)
    if g7.strict_letter(parsed.get("selected_letter")) in set(g7.LETTERS):
        return parsed
    text = json.dumps(parsed, ensure_ascii=False)
    patterns = (
        r"Option\s+([A-D]).{0,240}?intended answer",
        r"Option\s+([A-D]).{0,160}?corresponds to",
        r"Option\s+([A-D]).{0,160}?closest",
    )
    for pattern in patterns:
        matches = re.findall(pattern, text, re.I | re.S)
        if matches:
            return {
                "selected_letter": matches[-1].upper(),
                "confidence": "low",
                "deterministic_explicit_option_mapping": True,
                "prior_invalid_response": parsed,
            }
    return parsed


g7.complete_choice = _final_choice


if __name__ == "__main__":
    g7.main()
