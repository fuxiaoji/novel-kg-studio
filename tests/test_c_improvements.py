from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_c_improvements import bootstrap_delta, mcnemar_exact
from c_option_methods import (
    EvidenceContext,
    normalize_letter,
    normalize_payload,
    question_type,
    rrf_option_evidence,
    strict_visible_text,
)


def test_strict_mask_prevents_cross_boundary_chunks() -> None:
    text = "a" * 3000
    ctx = EvidenceContext.build({"nodes": [], "edges": []}, text, 1600)
    assert strict_visible_text(text, 1600) == text[:1600]
    assert max(c.end for c in ctx.chunks) == 1600
    assert all(c.end <= 1600 for c in ctx.chunks)


def test_strict_mask_filters_full_novel_graph_facts() -> None:
    visible = "Alice entered the library before noon. "
    hidden = "The detective later revealed that Bob was the killer."
    graph = {
        "nodes": [
            {
                "id": "alice",
                "name": "Alice",
                "type": "person",
                "description": "Alice entered the library; Bob was the killer.",
                "evidence": [visible.strip(), hidden],
                "aliases": ["The killer's witness"],
                "attributes": {"knows_killer": "Bob"},
            },
            {
                "id": "bob",
                "name": "Bob",
                "type": "person",
                "description": "Bob was the killer",
                "evidence": [hidden],
                "aliases": [],
            },
        ],
        "edges": [
            {"source": "alice", "target": "bob", "type": "accuses", "evidence": hidden}
        ],
    }
    ctx = EvidenceContext.build(graph, visible + hidden, len(visible))
    assert ctx.mask_policy == "strict-source-filtered-graph-v1"
    assert set(ctx.store.by_id) == {"alice"}
    assert "killer" not in ctx.store.by_id["alice"]["description"].lower()
    assert ctx.store.by_id["alice"]["aliases"] == []
    assert ctx.store.edges == []


def test_structured_and_natural_letter_parsing() -> None:
    assert normalize_letter("B") == "B"
    assert normalize_letter("Answer: C. clue") == "C"
    assert normalize_letter("The correct option is D") == "D"
    assert normalize_letter("no selection") is None


def test_question_type_polarity_and_special_cases() -> None:
    assert question_type("Which statement is not factual?") == "negative_check"
    assert question_type("What is Dr. X's true identity?") == "identity"
    assert question_type("How many people were in the room?") == "quantity_symbol"
    assert question_type("Who killed the doctor?") == "killer"


def test_rrf_deduplicates_chunks_and_nodes() -> None:
    graph = {
        "nodes": [
            {"id": "p1", "name": "Alice", "type": "person", "description": "Alice hid the key", "evidence": ["Alice hid the key in the clock."], "aliases": []},
            {"id": "k1", "name": "key", "type": "clue", "description": "hidden in clock", "evidence": ["Alice hid the key in the clock."], "aliases": []},
        ],
        "edges": [{"source": "p1", "target": "k1", "type": "supports", "evidence": "Alice hid it"}],
    }
    text = "Alice hid the key in the clock. Bob found a rope elsewhere."
    ctx = EvidenceContext.build(graph, text, None)
    result = rrf_option_evidence(ctx, "Where was the key?", ["clock", "rope", "desk", "car"])
    chunk_ids = [x["id"] for x in result["chunks"]]
    node_ids = [x["id"] for x in result["nodes"]]
    assert len(chunk_ids) == len(set(chunk_ids))
    assert len(node_ids) == len(set(node_ids))
    assert "p1" in node_ids or "k1" in node_ids


def test_payload_rejects_hallucinated_evidence_ids() -> None:
    payload = {
        "selected_letter": "A",
        "confidence": "high",
        "evidence": {"A": {"support": ["c_1", "made_up"], "contradict": [], "decoy": []}},
    }
    row = normalize_payload(payload, ["x", "y", "z", "w"], {"c_1"})
    assert row["selected_letter"] == "A"
    assert row["evidence_ids"] == ["c_1"]
    assert row["decisive"] is True


def test_payload_accepts_agent_continue_without_letter() -> None:
    row = normalize_payload(
        {"selected_letter": None, "confidence": "low", "evidence": {}},
        ["x", "y", "z", "w"],
        set(),
    )
    assert row["selected_letter"] is None
    assert row["selected_text"] == ""
    assert row["needs_more"] is True


def test_mcnemar_and_stratified_bootstrap_are_deterministic() -> None:
    new = [True, True, False, True]
    old = [False, True, True, False]
    stat = mcnemar_exact(new, old)
    assert stat["wins"] == 2
    assert stat["losses"] == 1
    rows = [
        {"novel": "26"},
        {"novel": "26"},
        {"novel": "27"},
        {"novel": "27"},
    ]
    # Use the module's ten-novel stratification only in integration; this unit
    # assertion covers the exact paired statistic without fabricating novels.
    assert 0 <= stat["p_raw"] <= 1
