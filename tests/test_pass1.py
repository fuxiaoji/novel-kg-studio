import json

from novel_kg_studio.chunking import find_span
from novel_kg_studio.pipeline.pass1_filter import parse_pass1_payload, run_pass1
from novel_kg_studio.schema import KeptSpan, parse_time_label


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, system, user, **kwargs):
        return self.payload


def test_parse_time_label_helper():
    assert parse_time_label("Day 1 morning") == (1, "morning")


def test_find_span():
    assert find_span("Poirot arrived at the villa.", "arrived at the villa") == (7, 27)


def test_parse_pass1_payload():
    from novel_kg_studio.chunking import TextChunk

    chunk = TextChunk("chunk_0", 10, 100, "Poirot arrived at the villa. The rain was heavy. He found a body.")
    kept, dropped, skipped = parse_pass1_payload(
        {
            "kept": [
                {"text": "Poirot arrived at the villa.", "time_label": "Day 1 morning"},
                {"text": "He found a body.", "time_label": "Day 1 morning"},
            ],
            "dropped": [{"text": "The rain was heavy.", "reason": "scene_setting"}],
        },
        chunk,
    )
    assert len(kept) == 2
    assert len(dropped) == 1
    assert dropped[0].reason == "scene_setting"
    assert kept[0].char_start == 10


def test_run_pass1_sorts_by_time(tmp_path):
    text = (
        "Poirot arrived on Day 1. The wind howled. He found a body on Day 2. "
        "The clouds were grey. Marta confessed on Day 3."
    )
    client = FakeClient(
        {
            "kept": [
                {"text": "Poirot arrived on Day 1.", "time_label": "Day 1"},
                {"text": "He found a body on Day 2.", "time_label": "Day 2"},
                {"text": "Marta confessed on Day 3.", "time_label": "Day 3"},
            ],
            "dropped": [
                {"text": "The wind howled.", "reason": "scene_setting"},
                {"text": "The clouds were grey.", "reason": "literary_description"},
            ],
        }
    )
    kept, dropped, stats = run_pass1(
        text,
        config={"chunking": {"size": 1500, "overlap": 100}, "model": {"max_tokens_pass1": 500}},
        client=client,
        out_dir=tmp_path,
        resume=False,
        workers=2,
        max_chunks=1,
        log=lambda msg: None,
    )
    assert len(kept) == 3
    assert [s.day for s in kept] == [1, 2, 3]
    assert stats["dropped_chars"] > 0
    assert len(dropped) == 2
