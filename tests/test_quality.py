from novel_kg_studio.pipeline.quality import evaluate_graph_quality


def _node(index: int, degree: int) -> dict:
    return {
        "id": f"n{index}",
        "degree": degree,
        "evidence": [f"evidence {index}"],
        "source_sentence_ids": [index],
    }


def test_quality_gate_accepts_connected_graph():
    graph = {
        "nodes": [_node(1, 1), _node(2, 2), _node(3, 1)],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
        "merge_stats": {"dropped_relations": 0, "deduplicated_relations": 0},
        "consolidation_stats": {},
    }
    report = evaluate_graph_quality(graph)
    assert report["passed"] is True
    assert report["metrics"]["isolate_rate"] == 0.0


def test_quality_gate_rejects_sparse_graph():
    graph = {
        "nodes": [_node(index, 0) for index in range(1, 11)],
        "edges": [{"source": "n1", "target": "n2"}],
        "merge_stats": {"dropped_relations": 10, "deduplicated_relations": 0},
        "consolidation_stats": {},
    }
    report = evaluate_graph_quality(graph)
    assert report["passed"] is False
    assert any("isolate_rate" in failure for failure in report["failures"])
    assert any("edge_node_ratio" in failure for failure in report["failures"])
