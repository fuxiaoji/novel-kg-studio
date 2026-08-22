from novel_kg_studio.pipeline.coref import repair_graph


class FakeClient:
    def __init__(self, mapping):
        self.mapping = mapping

    def complete_json(self, system, prompt, **kwargs):
        return self.mapping


def test_coref_repair_reattaches_edge(tmp_path):
    graph = {
        "nodes": [
            {"id": "n1", "name": "Hastings", "type": "person", "aliases": [], "evidence": [], "degree": 2},
            {"id": "n2", "name": "the narrator", "type": "person", "aliases": [], "evidence": ["I"], "degree": 2},
            {"id": "n3", "name": "Britain", "type": "location", "aliases": [], "evidence": [], "degree": 1},
        ],
        "edges": [
            {
                "id": "e1",
                "source": "n2",
                "target": "n3",
                "type": "located_at",
                "evidence": "I have spent most of my time in Britain",
                "confidence": 0.9,
            }
        ],
    }
    kept = [{"seq": 0, "text": "I have spent most of my time in Britain."}]
    repaired, stats = repair_graph(graph, kept, FakeClient({"0": "Hastings"}), tmp_path, resume=False, log=lambda msg: None)
    assert repaired["edges"][0]["source"] == "n1"
    assert stats["moved_edges"] == 1
