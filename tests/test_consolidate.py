from novel_kg_studio.pipeline.consolidate import consolidate_person_nodes


class FakeClient:
    def __init__(self, groups):
        self.groups = groups

    def complete_json(self, system, prompt, **kwargs):
        return self.groups


def test_consolidate_merges_variant_nodes(tmp_path):
    graph = {
        "nodes": [
            {"id": "n1", "name": "Marte Dobroil", "type": "person", "aliases": [], "evidence": ["stabbed"], "degree": 5, "salience": 5, "description": "a", "mention_count": 4},
            {"id": "n2", "name": "Malt Dobré", "type": "person", "aliases": [], "evidence": ["the woman who killed"], "degree": 2, "salience": 4, "description": "b", "mention_count": 3},
            {"id": "n3", "name": "Hercule Poirot", "type": "person", "aliases": [], "evidence": [], "degree": 10, "salience": 5, "description": "", "mention_count": 1},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n3", "type": "related_to", "evidence": "x", "confidence": 0.9, "decoy": False, "importance": 3},
            {"id": "e2", "source": "n2", "target": "n3", "type": "related_to", "evidence": "y", "confidence": 0.9, "decoy": False, "importance": 3},
        ],
    }
    client = FakeClient([{"canonical": "Marte Dobroil", "members": ["Marte Dobroil", "Malt Dobré"]}])
    consolidated, stats = consolidate_person_nodes(graph, client, tmp_path, resume=False, log=lambda msg: None)
    assert stats["merged_nodes"] == 1
    assert len(consolidated["nodes"]) == 2
    ids = {n["id"] for n in consolidated["nodes"]}
    assert ids == {"n1", "n3"}
    for edge in consolidated["edges"]:
        assert edge["source"] != "n2" and edge["target"] != "n2"
    canonical = next(n for n in consolidated["nodes"] if n["id"] == "n1")
    assert "the woman who killed" in canonical["evidence"]


def test_consolidate_handles_overlapping_groups(tmp_path):
    graph = {
        "nodes": [
            {"id": "n1", "name": "Marte", "type": "person", "aliases": [], "evidence": ["a"], "degree": 5, "salience": 5, "description": "", "mention_count": 1},
            {"id": "n2", "name": "Malt", "type": "person", "aliases": [], "evidence": ["b"], "degree": 3, "salience": 4, "description": "", "mention_count": 1},
            {"id": "n3", "name": "Miss Marte", "type": "person", "aliases": [], "evidence": ["c"], "degree": 2, "salience": 3, "description": "", "mention_count": 1},
            {"id": "n4", "name": "Poirot", "type": "person", "aliases": [], "evidence": [], "degree": 10, "salience": 5, "description": "", "mention_count": 1},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n4", "type": "related_to", "evidence": "x", "confidence": 0.9, "decoy": False, "importance": 3},
            {"id": "e2", "source": "n2", "target": "n4", "type": "related_to", "evidence": "y", "confidence": 0.9, "decoy": False, "importance": 3},
            {"id": "e3", "source": "n3", "target": "n4", "type": "related_to", "evidence": "z", "confidence": 0.9, "decoy": False, "importance": 3},
        ],
    }
    client = FakeClient(
        [
            {"canonical": "Marte", "members": ["Marte", "Malt"]},
            {"canonical": "Miss Marte", "members": ["Malt", "Miss Marte"]},
        ]
    )
    consolidated, stats = consolidate_person_nodes(graph, client, tmp_path, resume=False, log=lambda msg: None)
    assert stats["merged_nodes"] == 1
    assert len(consolidated["nodes"]) == 3
    assert consolidated["edges"][0]["source"] in {"n1", "n4"}


def test_consolidate_does_not_remap_an_existing_canonical(tmp_path):
    graph = {
        "nodes": [
            {"id": "n1", "name": "Marte", "type": "person", "aliases": [], "evidence": ["a"], "degree": 5, "salience": 5, "description": "", "mention_count": 1},
            {"id": "n2", "name": "Malt", "type": "person", "aliases": [], "evidence": ["b"], "degree": 3, "salience": 4, "description": "", "mention_count": 1},
            {"id": "n3", "name": "Miss Marte", "type": "person", "aliases": [], "evidence": ["c"], "degree": 9, "salience": 3, "description": "", "mention_count": 1},
            {"id": "n4", "name": "Poirot", "type": "person", "aliases": [], "evidence": [], "degree": 10, "salience": 5, "description": "", "mention_count": 1},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n4", "type": "related_to", "evidence": "x", "confidence": 0.9, "decoy": False, "importance": 3},
            {"id": "e2", "source": "n2", "target": "n4", "type": "related_to", "evidence": "y", "confidence": 0.9, "decoy": False, "importance": 3},
            {"id": "e3", "source": "n3", "target": "n4", "type": "related_to", "evidence": "z", "confidence": 0.9, "decoy": False, "importance": 3},
        ],
    }
    client = FakeClient(
        [
            {"canonical": "Marte", "members": ["Marte", "Malt"]},
            {"canonical": "Miss Marte", "members": ["Marte", "Miss Marte"]},
        ]
    )
    consolidated, stats = consolidate_person_nodes(
        graph, client, tmp_path, resume=False, log=lambda msg: None
    )
    assert stats["merged_nodes"] == 2
    assert {node["id"] for node in consolidated["nodes"]} == {"n1", "n4"}
    assert all(edge["source"] in {"n1", "n4"} for edge in consolidated["edges"])


def test_consolidate_rejects_lexically_incompatible_people(tmp_path):
    graph = {
        "nodes": [
            {"id": "n1", "name": "Mrs. Adler", "type": "person", "aliases": [], "evidence": ["a"], "degree": 8, "salience": 5, "description": "", "mention_count": 1},
            {"id": "n2", "name": "Mr. Davenheim", "type": "person", "aliases": [], "evidence": ["b"], "degree": 3, "salience": 4, "description": "", "mention_count": 1},
            {"id": "n3", "name": "Lady Adler", "type": "person", "aliases": [], "evidence": ["c"], "degree": 2, "salience": 4, "description": "", "mention_count": 1},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2", "type": "related_to", "evidence": "x", "confidence": 0.9, "decoy": False, "importance": 3},
            {"id": "e2", "source": "n3", "target": "n2", "type": "related_to", "evidence": "y", "confidence": 0.9, "decoy": False, "importance": 3},
        ],
    }
    client = FakeClient(
        [{"canonical": "Mrs. Adler", "members": ["Mrs. Adler", "Mr. Davenheim", "Lady Adler"]}]
    )
    consolidated, stats = consolidate_person_nodes(
        graph, client, tmp_path, resume=False, log=lambda msg: None
    )
    assert stats["merged_nodes"] == 1
    assert {node["id"] for node in consolidated["nodes"]} == {"n1", "n2"}
