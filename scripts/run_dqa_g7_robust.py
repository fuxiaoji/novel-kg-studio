"""Robust G7 entry point with graph-only alias-to-option remapping."""

from __future__ import annotations

import run_dqa_g7 as schema_entry  # noqa: F401  # installs schema compatibility
import run_dqa_g7_pure_graph as g7


_complete_choice = g7.complete_choice


def _robust_choice(client, system: str, user: str, max_tokens: int = 320):
    parsed = _complete_choice(client, system, user, max_tokens=max_tokens)
    if g7.strict_letter(parsed.get("selected_letter")) in set(g7.LETTERS):
        return parsed
    prior = str(parsed.get("raw") or parsed)[:5000]
    remap_prompt = (
        user + "\n\nYOUR PRIOR GRAPH-ONLY CONCLUSION\n" + prior
        + "\n\nMap the entity or alias in that conclusion to the closest listed option. "
          "Do not retrieve new evidence and do not abstain. Return only JSON: {\"selected_letter\":\"A|B|C|D\"}."
    )
    remapped = g7.parse_object(client.complete("You only map an existing conclusion to one option letter.", remap_prompt, max_tokens=80))
    remapped["alias_remap_after_invalid_letter"] = True
    remapped["prior_invalid_response"] = parsed
    return remapped


g7.complete_choice = _robust_choice


if __name__ == "__main__":
    g7.main()
