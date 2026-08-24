"""Baseline-blind G7 with the successful tight G6 evidence protocol."""

from __future__ import annotations

import run_dqa_g7_final as final_entry  # noqa: F401
import run_dqa_g7_pure_graph as g7
from run_dqa30_g6_graph_expansion import answer_prompt


g7.prompt = answer_prompt


if __name__ == "__main__":
    g7.main()
