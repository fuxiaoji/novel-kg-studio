import json

from run_dqa30_batch_eval import (
    exact_mcnemar,
    majority_or_tail,
    map_to_original,
)


def test_permutation_mapping_and_majority():
    assert map_to_original("A", [3, 2, 1, 0]) == "D"
    assert map_to_original("D", [1, 2, 3, 0]) == "A"
    assert majority_or_tail(["B", "B", "C"], "D") == ("B", "graph_majority")
    assert majority_or_tail(["A", "B", "C"], "D") == ("D", "tail_fallback")


def test_exact_mcnemar_counts_paired_wins_and_losses():
    rows = [
        {"correct": {"G5": True, "B1": False}},
        {"correct": {"G5": True, "B1": False}},
        {"correct": {"G5": False, "B1": True}},
        {"correct": {"G5": True, "B1": True}},
    ]
    result = exact_mcnemar(rows, "G5", "B1")
    assert result["wins"] == 2
    assert result["losses"] == 1
    assert 0.0 <= result["exact_p"] <= 1.0
