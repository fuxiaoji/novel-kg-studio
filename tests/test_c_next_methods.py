from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from c_next_methods import (
    _remap_payload,
    canonical_citation,
    canonicalize_payload,
    method_c3_first_stage,
    method_c3_discriminative,
    permutation_order,
    run_evidence_verifier,
)
from c_option_methods import EvidenceContext


CHUNKS = [
    {"id": "c_3", "start": 100, "end": 200, "text": "The blue caps were the guards posted outside the museum."},
    {"id": "c_4", "start": 200, "end": 300, "text": "A separate irrelevant paragraph."},
]


def test_canonical_citation_accepts_decorated_id_and_quote():
    valid = {"c_3", "c_4"}
    assert canonical_citation("c_3 @ 120: quoted", CHUNKS, valid) == "c_3"
    assert canonical_citation({"start": 130, "quote": "guards"}, CHUNKS, valid) == "c_3"
    assert canonical_citation("The blue caps were the guards posted outside the museum.", CHUNKS, valid) == "c_3"


def test_canonicalize_payload_preserves_localized_evidence():
    raw = {"selected_letter": "D", "evidence": {"D": {"support": ["c_3 @ 100"], "contradict": [], "decoy": []}}}
    normalized = canonicalize_payload(raw, ["a", "b", "c", "guards"], CHUNKS)
    assert normalized["selected_letter"] == "D"
    assert normalized["evidence_ids"] == ["c_3"]
    assert normalized["decisive"] is True


def test_permutation_mapping_and_determinism():
    q = {"qid": "x", "question": "Who?", "choices": ["a", "b", "c", "d"]}
    order = permutation_order(q)
    assert order == permutation_order(q)
    assert order != [0, 1, 2, 3]
    raw = {"selected_letter": "A", "evidence": {"A": {"support": ["c_3"]}}}
    mapped = _remap_payload(raw, [2, 0, 3, 1], q["choices"])
    assert mapped["selected_letter"] == "C"
    assert mapped["evidence"]["C"]["support"] == ["c_3"]


class FakeClient:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = 0

    def complete_json(self, *_args, **_kwargs):
        self.calls += 1
        return self.rows.pop(0)


def _ctx():
    text = "The blue caps were guards. " * 200
    return EvidenceContext.build({"nodes": [], "edges": []}, text, None)


def test_c3_stops_after_decisive_first_stage():
    raw = {
        "selected_letter": "A",
        "evidence": {
            "A": {"support": ["c_0"], "contradict": [], "decoy": []},
            "B": {"support": [], "contradict": ["c_0"], "decoy": []},
        },
    }
    client = FakeClient([raw])
    q = {"question": "Who were the blue caps?", "choices": ["guards", "visitors", "writers", "drivers"]}
    result = method_c3_discriminative(client, q, _ctx())
    assert client.calls == 1
    assert result["stopped_after_stage"] == 1


def test_c3_uses_at_most_one_followup_stage():
    first = {"selected_letter": "A", "evidence": {"A": {"support": ["c_0"]}}, "followup_queries": ["guards", "visitors"]}
    second = {"selected_letter": "A", "evidence": {"A": {"support": ["c_0"]}}}
    client = FakeClient([first, second])
    q = {"question": "Who were the blue caps?", "choices": ["guards", "visitors", "writers", "drivers"]}
    result = method_c3_discriminative(client, q, _ctx())
    assert client.calls == 2
    assert result["stopped_after_stage"] == 2


def test_c3_first_stage_always_uses_one_call():
    raw = {"selected_letter": "A", "evidence": {"A": {"support": ["c_0"]}}}
    client = FakeClient([raw])
    q = {"question": "Who were the blue caps?", "choices": ["guards", "visitors", "writers", "drivers"]}
    result = method_c3_first_stage(client, q, _ctx())
    assert client.calls == 1
    assert result["stopped_after_stage"] == 1


def test_verifier_direct_gate_avoids_extra_model_call():
    base = {
        "selected_letter": "A",
        "evidence_ids": ["c_3"],
        "evidence": {"A": {"support": ["c_3"], "contradict": [], "decoy": []}},
        "retrieval": {"chunks": CHUNKS},
    }
    perm = {**base, "method": "c1_option_permutation", "permutation_consistent": True}
    other = {**base, "method": "c4_citation_fixed"}
    client = FakeClient([])
    q = {"qid": "x", "question": "Who were the blue caps?", "choices": ["guards", "visitors", "writers", "drivers"]}
    result = run_evidence_verifier(client, q, [perm, other], "B")
    assert client.calls == 0
    assert result["gated_direct"] is True
    assert result["selected_letter"] == "A"
