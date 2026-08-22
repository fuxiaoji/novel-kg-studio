from novel_kg_studio.store import GraphStore


def _graph():
    nodes = [
        {"id": "n1", "name": "window", "type": "clue_object", "aliases": [], "evidence": ["He climbed out of the window."], "text_pos": 0.8, "time_pos": 0.9},
        {"id": "n2", "name": "front door", "type": "clue_object", "aliases": [], "evidence": ["He left the front door half open."], "text_pos": 0.7, "time_pos": 0.9},
        {"id": "n3", "name": "gardener", "type": "person", "aliases": [], "evidence": ["The gardener planted flowers."], "text_pos": 0.4, "time_pos": 0.5},
    ]
    edges = [
        {"id": "e1", "source": "n1", "target": "n2", "type": "contradicts", "evidence": "window vs door", "confidence": 0.9},
        {"id": "e2", "source": "n1", "target": "n3", "type": "witnessed_by", "evidence": "gardener saw", "confidence": 0.7},
    ]
    return nodes, edges


def test_retrieve_first_and_second_order():
    nodes, edges = _graph()
    store = GraphStore(nodes, edges)
    result = store.retrieve("killer left through the window", k1=2, k2=5)
    assert set(result.first_order) == {"n1", "n2"}
    assert "n3" in result.second_order
    sub_ids = {n["id"] for n in result.subgraph["nodes"]}
    assert sub_ids == {"n1", "n2", "n3"}
    for edge in result.subgraph["edges"]:
        assert edge["source"] in sub_ids and edge["target"] in sub_ids


def test_third_order_expansion():
    nodes = [
        {"id": "n1", "name": "killer", "type": "person", "aliases": [], "evidence": ["the killer left through the window"], "text_pos": 0.5, "time_pos": 0.5},
        {"id": "n2", "name": "window", "type": "clue_object", "aliases": [], "evidence": ["through the window"], "text_pos": 0.6, "time_pos": 0.5},
        {"id": "n3", "name": "flower bed", "type": "location", "aliases": [], "evidence": ["footprints in the flower bed"], "text_pos": 0.7, "time_pos": 0.5},
    ]
    edges = [
        {"id": "e1", "source": "n1", "target": "n2", "type": "means", "evidence": "x", "confidence": 0.9},
        {"id": "e2", "source": "n2", "target": "n3", "type": "located_at", "evidence": "y", "confidence": 0.8},
    ]
    store = GraphStore(nodes, edges)
    result = store.retrieve("killer window", k1=1, k2=5, k3=5)
    assert result.first_order == ["n1"]
    assert result.second_order == ["n2"]
    assert result.third_order == ["n3"]
