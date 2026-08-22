from novel_kg_studio.pipeline.merge import build_graph
from novel_kg_studio.schema import KeptSpan


def _span(seq, text, day):
    return KeptSpan(text=text, chunk_idx=0, span_idx=seq, char_start=0, char_end=len(text), day=day, seq=seq, time_position=0.5, text_position=0.5)


def test_merge_resolves_aliases_and_dedupes():
    spans = [
        _span(0, "Poirot said he stayed at the villa last night.", 1),
        _span(1, "Marta slipped out through the back door.", 2),
    ]
    kept_by_seq = {s.seq: s for s in spans}
    records = [
        {
            "line_indices": [0, 1],
            "entities": [
                {"name": "Hercule Poirot", "type": "person", "aliases": ["Poirot"], "mentions": [{"text": "Poirot said", "sentence_index": 0}]},
                {"name": "villa", "type": "location", "aliases": [], "mentions": [{"text": "the villa", "sentence_index": 0}]},
                {"name": "Marta", "type": "person", "aliases": [], "mentions": [{"text": "Marta", "sentence_index": 1}]},
            ],
            "relations": [
                {"source": "Hercule Poirot", "target": "villa", "type": "located_at", "evidence": "stayed at the villa", "sentence_index": 0, "confidence": 0.9},
                {"source": "Poirot", "target": "villa", "type": "located_at", "evidence": "stayed at the villa", "sentence_index": 0, "confidence": 0.8},
                {"source": "Poirot", "target": "Marta", "type": "related_to", "evidence": "they fought loudly", "sentence_index": 1, "confidence": 0.5},
            ],
        }
    ]
    nodes, edges, stats = build_graph(records, kept_by_seq, novel_len=1000, log=lambda msg: None)
    assert stats["num_nodes"] == 3
    assert stats["num_edges"] == 1
    assert stats["deduplicated_relations"] == 1
    assert stats["dropped_relations"] == 1
    poirot = next(n for n in nodes if n["name"] == "Hercule Poirot")
    assert poirot["type"] == "person"

def test_merge_two_pass_recovers_ellipsis_and_prunes_ungrounded_isolate():
    spans = [
        _span(0, "Detective Poirot quietly stayed at the country villa overnight.", 1),
    ]
    records = [
        {
            "entities": [
                {
                    "name": "Detective Poirot",
                    "type": "person",
                    "aliases": ["Poirot"],
                    "mentions": [{"text": "Detective Poirot", "sentence_index": 0}],
                },
                {
                    "name": "unused object",
                    "type": "clue_object",
                    "aliases": [],
                    "mentions": [{"text": "not in source", "sentence_index": 0}],
                },
            ],
            "relations": [
                {
                    "source": "Poirot",
                    "target": "country villa",
                    "type": "located_at",
                    "evidence": "Detective Poirot...the country villa",
                    "sentence_index": 0,
                    "confidence": 0.9,
                }
            ],
        },
        {
            "entities": [
                {
                    "name": "country villa",
                    "type": "location",
                    "aliases": [],
                    "mentions": [{"text": "country villa", "sentence_index": 0}],
                }
            ],
            "relations": [],
        },
    ]
    nodes, edges, stats = build_graph(records, {0: spans[0]}, novel_len=1000, log=lambda msg: None)
    assert {node["name"] for node in nodes} == {"Detective Poirot", "country villa"}
    assert len(edges) == 1
    assert "quietly stayed at the country villa" in edges[0]["evidence"]
    assert stats["recovered_relation_evidence"] == 1
    assert stats["dropped_relation_endpoints"] == 0
    assert stats["pruned_ungrounded_isolates"] == 1
