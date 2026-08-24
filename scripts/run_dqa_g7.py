"""Schema-compatible entry point for the baseline-blind G7 experiment."""

from __future__ import annotations

import run_dqa_g7_pure_graph as g7


_merged_cases = g7.merged_cases


def _compatible_cases(novels):
    cases = _merged_cases(novels)
    for case in cases.values():
        for question in case["questions"]:
            question["answer_letter"] = g7.LETTERS[int(question["gold_index"])]
    return cases


g7.merged_cases = _compatible_cases


if __name__ == "__main__":
    g7.main()
