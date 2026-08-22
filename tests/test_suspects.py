from novel_kg_studio.store import GraphStore
from novel_kg_studio.store.suspects import is_who_question, suspect_chain


def _store():
    nodes = [
        {"id": "n1", "name": "Marte", "type": "person", "aliases": [], "evidence": [], "degree": 3},
        {"id": "n2", "name": "Jack", "type": "person", "aliases": [], "evidence": [], "degree": 3},
        {"id": "n3", "name": "Mr. Renault", "type": "person", "aliases": [], "evidence": [], "degree": 5},
    ]
    edges = [
        {"id": "e1", "source": "n1", "target": "n3", "type": "motive", "evidence": "wanted the fortune", "confidence": 0.9, "decoy": False, "importance": 4},
        {"id": "e2", "source": "n2", "target": "n3", "type": "supports", "evidence": "confessed to protect someone", "confidence": 0.9, "decoy": True, "importance": 3},
    ]
    return GraphStore(nodes, edges)


def test_is_who_question():
    assert is_who_question("Who killed Mr. Renault?")
    assert is_who_question("The thief of the diamond is ( )")
    assert not is_who_question("What weapon was used?")


def test_suspect_chain_ranks_non_decoy_first():
    store = _store()
    rows = suspect_chain(store, "Who killed Mr. Renault?")
    assert rows[0]["name"] == "Marte"
    assert rows[0]["score"] > rows[1]["score"]
    assert rows[1]["edges"][0]["decoy"] is True
