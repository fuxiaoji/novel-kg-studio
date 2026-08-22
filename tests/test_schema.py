from novel_kg_studio.schema import canonical_name, parse_time_label


def test_parse_time_label():
    assert parse_time_label("Day 2, morning") == (2, "morning")
    assert parse_time_label("D3 night") == (3, "night")
    assert parse_time_label("unknown") == (None, "unknown")


def test_canonical_name():
    assert canonical_name("Hercule Poirot") == "hercule poirot"
    assert canonical_name("The Villa") == "villa"

