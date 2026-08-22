from scripts.run_c12_consensus import consensus


def test_clear_majority_wins():
    selected, counts = consensus({"tail": "A", "c4": "B", "c6": "B", "c8_graph": "B"})
    assert selected == "B"
    assert counts == {"A": 1, "B": 3}


def test_c4_breaks_two_two_tie():
    selected, _ = consensus({"tail": "A", "c4": "B", "c6": "A", "c8_graph": "B"})
    assert selected == "B"


def test_c4_breaks_four_way_tie():
    selected, _ = consensus({"tail": "A", "c4": "C", "c6": "B", "c8_graph": "D"})
    assert selected == "C"


def test_invalid_votes_are_ignored():
    selected, counts = consensus({"tail": None, "c4": "answer: D", "c6": "", "c8_graph": "D"})
    assert selected == "D"
    assert counts == {"D": 2}
