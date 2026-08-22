from novel_kg_studio.schema import DroppedSpan, KeptSpan
from novel_kg_studio.viz.rulers import text_ruler_density, time_over_text_ruler, time_ruler_density
from novel_kg_studio.viz.views3d import view_density, view_text_time_type
from novel_kg_studio.viz.llm_process import llm_process_panel


def _spans():
    return [
        KeptSpan(text="a" * 20, chunk_idx=0, span_idx=0, char_start=0, char_end=20, day=1, seq=0, time_position=0.1, text_position=0.1),
        KeptSpan(text="b" * 30, chunk_idx=0, span_idx=1, char_start=100, char_end=130, day=2, seq=1, time_position=0.9, text_position=0.4),
    ]


def test_density_figures_have_data():
    fig1 = text_ruler_density(_spans())
    fig2 = time_ruler_density(
        [
            {"id": "n1", "type": "person", "time_pos": 0.1},
            {"id": "n2", "type": "clue_object", "time_pos": 0.9},
        ]
    )
    assert len(fig1.data) >= 2
    assert len(fig2.data) >= 2


def test_time_over_text_ruler_has_scatter_and_line():
    fig = time_over_text_ruler(_spans())
    assert len(fig.data) >= 2
    assert any(trace.type == "scatter" for trace in fig.data)


def test_llm_process_panel_embeds_rows():
    html = llm_process_panel(
        [
            {
                "question": "Q?",
                "mask": 1.0,
                "interpretation": "x",
                "expanded_query": "q",
                "entity_targets": [],
                "first_order": ["a"],
                "second_order": ["b"],
                "third_order": ["c"],
                "third_order_informative_ratio": 0.5,
                "gold_coverage": {"first_second": 0.1, "first_second_third": 0.2},
                "answer": "ans",
            }
        ]
    )
    assert "llm-proc-select" in html
    assert "Q?" in html


def test_3d_views_build():
    nodes = [
        {"id": "n1", "name": "Poirot", "type": "person", "aliases": [], "evidence": ["x"], "text_pos": 0.1, "time_pos": 0.2, "degree": 1},
        {"id": "n2", "name": "villa", "type": "location", "aliases": [], "evidence": ["y"], "text_pos": 0.3, "time_pos": 0.4, "degree": 1},
    ]
    edges = [{"id": "e1", "source": "n1", "target": "n2", "type": "located_at", "evidence": "at", "confidence": 0.9}]
    fig_a, state_a = view_text_time_type(nodes, edges)
    assert len(fig_a.data) >= 2
    assert state_a["edge_traces"][0]["t"] == 0.4
    fig_c = view_density(nodes)
    assert fig_c.data[0].type == "surface"
