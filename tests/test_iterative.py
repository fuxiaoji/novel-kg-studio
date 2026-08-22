from novel_kg_studio.store.iterative import run_iterative


class FakeClient:
    def __init__(self, verdict):
        self.verdict = verdict

    def complete_json(self, system, prompt, **kwargs):
        return self.verdict


def test_run_iterative_prefers_supported():
    client = FakeClient({"verdict": "supported", "confidence": 0.9, "reason": "evidence"})
    results = run_iterative(
        client,
        question="Who killed Mr. Renault?",
        candidates=[{"name": "Marte"}, {"name": "Jack"}],
        evidence_by_candidate={"Marte": ["evidence"], "Jack": ["evidence"]},
        clues=["clue"],
        cache_dir=None,
        key="k",
        who=True,
    )
    assert results[0]["candidate"] == "Marte"
    assert results[0]["state"] == "accepted"
