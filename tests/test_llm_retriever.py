from novel_kg_studio.store import GraphStore
from novel_kg_studio.store.bm25 import BM25Index
from novel_kg_studio.store.llm_retriever import SearchPlan, execute, plan_search, top_sentences


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, system, prompt, **kwargs):
        return self.payload


def _store():
    nodes = [
        {"id": "n1", "name": "killer", "type": "person", "aliases": [], "description": "", "salience": 5, "attributes": {}, "evidence": ["the killer left through the window"], "text_pos": 0.5, "time_pos": 0.5},
        {"id": "n2", "name": "window", "type": "clue_object", "aliases": [], "description": "exit", "salience": 4, "attributes": {}, "evidence": ["climbed out of the window"], "text_pos": 0.6, "time_pos": 0.5},
        {"id": "n3", "name": "front door", "type": "clue_object", "aliases": [], "description": "decoy", "salience": 2, "attributes": {}, "evidence": ["half-open illusion"], "text_pos": 0.6, "time_pos": 0.5},
    ]
    edges = [
        {"id": "e1", "source": "n1", "target": "n2", "type": "means", "evidence": "x", "confidence": 0.9, "decoy": False, "importance": 4},
        {"id": "e2", "source": "n1", "target": "n3", "type": "supports", "evidence": "illusion", "confidence": 0.9, "decoy": True, "importance": 2},
    ]
    return GraphStore(nodes, edges)


def test_plan_search_parses_plan():
    store = _store()
    client = FakeClient(
        {
            "search_terms": "killer exit window",
            "target_types": ["clue_object"],
            "entity_targets": ["window"],
            "hypothetical_clue": "The killer climbed out of the window.",
            "follow_up_terms": "footprints flower bed",
        }
    )
    plan = plan_search(client, "How did the killer leave?", store)
    assert plan.entity_targets == ["window"]
    assert "climbed" in plan.hypothetical_clue


def test_execute_prefers_non_decoy_and_salience():
    store = _store()
    plan = SearchPlan(
        question="killer window",
        search_terms="killer window exit",
        hypothetical_clue="climbed out of the window",
        entity_targets=["window"],
    )
    first, second = execute(store, plan)
    combined = first + second
    assert "n1" in combined and "n2" in combined
    assert combined.index("n2") < combined.index("n3")


def test_top_sentences_returns_hits():
    index = BM25Index(["the killer climbed out of the window", "the gardener planted flowers"])
    plan = SearchPlan(question="killer window", search_terms="window", hypothetical_clue="")
    rows = top_sentences(index, "killer window", plan, k=2)
    assert rows[0][0] == 0
