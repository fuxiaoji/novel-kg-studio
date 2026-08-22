from scripts.run_minimax_tail_10novels import build_user_prompt, initial_tail_chars, parse_answer


def test_initial_tail_keeps_suffix_budget_and_whole_short_text():
    assert initial_tail_chars("x" * 100) == 100
    assert initial_tail_chars("x" * 1_000_000) < 1_000_000


def test_prompt_places_novel_before_variable_question_for_cache_prefix():
    q = {"question": "Who did it?", "choices": ["a", "b", "c", "d"]}
    prompt = build_user_prompt("THE NOVEL", q)
    assert prompt.index("THE NOVEL") < prompt.index("Who did it?")


def test_parse_answer_strict_json_and_fallback():
    choices = ["one", "two", "three", "four"]
    assert parse_answer('{"selected_letter":"C","confidence":"high","reason":"e"}', choices)["selected_text"] == "three"
    assert parse_answer("Final answer: B", choices)["selected_letter"] == "B"
