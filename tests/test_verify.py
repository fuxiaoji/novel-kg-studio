from novel_kg_studio.store.verify import best_candidate, role_for_question, verify_candidates


class FakeClient:
    def __init__(self, verdict):
        self.verdict = verdict

    def complete_json(self, system, prompt, **kwargs):
        return self.verdict


def test_role_for_question():
    assert "killer of Mr. Renault" in role_for_question("Who killed Mr. Renault?")
    assert "accomplice" in role_for_question("Who was the accomplice of the killer?")


def test_best_candidate_picks_supported():
    verdicts = [
        {"candidate": "Jack", "verdict": "refute", "confidence": 0.9},
        {"candidate": "Marte", "verdict": "support", "confidence": 0.8},
    ]
    assert best_candidate(verdicts) == "Marte"
    assert best_candidate([{"candidate": "Jack", "verdict": "unknown", "confidence": 0.5}]) is None


def test_verify_candidates_caches(tmp_path):
    client = FakeClient({"verdict": "support", "confidence": 0.9, "reason": "evidence"})
    verdicts = verify_candidates(
        client,
        "Who killed Mr. Renault?",
        ["Marte", "Jack"],
        ["evidence sentence"],
        ["clue"],
        tmp_path,
        "key",
    )
    assert len(verdicts) == 2
    assert verdicts[0]["verdict"] == "support"
    cached = verify_candidates(
        client,
        "Who killed Mr. Renault?",
        ["Marte"],
        [],
        [],
        tmp_path,
        "key",
    )
    assert cached[0]["candidate"] == "Marte"
