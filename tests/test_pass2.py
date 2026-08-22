from novel_kg_studio.pipeline.pass2_graph import build_pass2_user, parse_pass2_payload, parse_pass2_payload_v2


def test_build_pass2_user_numbers_lines():
    user = build_pass2_user([(0, "Poirot arrived."), (3, "Marta left.")])
    assert "[0] Poirot arrived." in user
    assert "[3] Marta left." in user


def test_parse_pass2_payload_filters():
    payload = {
        "entities": [
            {"name": "Poirot", "type": "person", "aliases": ["Hercule"], "mentions": [{"text": "Poirot", "sentence_index": 0}]},
            {"name": "bad", "type": "nonsense", "mentions": []},
        ],
        "relations": [
            {"source": "Poirot", "target": "villa", "type": "located_at", "evidence": "at the villa", "sentence_index": 0, "confidence": 0.9},
            {"source": "Poirot", "target": "villa", "type": "unknown_type", "evidence": "x", "sentence_index": 0},
            {"source": "Poirot", "target": "villa", "type": "located_at", "evidence": "y", "sentence_index": -1},
        ],
    }
    entities, relations = parse_pass2_payload(payload)
    assert len(entities) == 1
    assert len(relations) == 1


def test_parse_pass2_payload_v2_captures_rich_fields():
    payload = {
        "entities": [
            {
                "name": "the window",
                "type": "location",
                "aliases": ["window"],
                "description": "the bedroom window with a tree beside it",
                "salience": 5,
                "attributes": {"role": "exit route"},
                "mentions": [{"text": "the window", "sentence_index": 0}],
            }
        ],
        "relations": [
            {
                "source": "the window",
                "target": "the killer",
                "type": "means",
                "evidence": "climbed out of the window",
                "sentence_index": 0,
                "confidence": 0.9,
                "decoy": False,
                "importance": 4,
            },
            {
                "source": "the front door",
                "target": "the killer",
                "type": "supports",
                "evidence": "left the front door half-open to create the illusion",
                "sentence_index": 0,
                "confidence": 0.9,
                "decoy": True,
                "importance": 2,
            },
        ],
    }
    entities, relations = parse_pass2_payload_v2(payload)
    assert entities[0]["description"].startswith("the bedroom window")
    assert entities[0]["salience"] == 5
    assert entities[0]["attributes"] == {"role": "exit route"}
    assert relations[1]["decoy"] is True
