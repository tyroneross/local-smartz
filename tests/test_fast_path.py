"""Tests for the fast-path router heuristic.

Pure-function tests — no LLM calls, no network.
"""

from localsmartz.profiles import is_fast_path


# ── True cases: trivial prompts ──

def test_simple_math_is_fast_path():
    assert is_fast_path("what is 2+2?") is True


def test_hello_is_fast_path():
    assert is_fast_path("hello") is True


def test_define_term_is_fast_path():
    assert is_fast_path("define entropy") is True


def test_short_question_is_fast_path():
    assert is_fast_path("who wrote Hamlet?") is True


def test_single_sentence_statement_is_fast_path():
    assert is_fast_path("give me a fun fact") is True


# ── False cases: research-like or heavy prompts ──

def test_research_keyword_blocks_fast_path():
    assert is_fast_path("research competitors for Stripe") is False


def test_analyze_keyword_blocks_fast_path():
    assert is_fast_path("analyze the Q3 earnings") is False


def test_compare_keyword_blocks_fast_path():
    assert is_fast_path("compare Python and Rust") is False


def test_summarize_keyword_blocks_fast_path():
    assert is_fast_path("summarize this document") is False


def test_write_a_keyword_blocks_fast_path():
    # "write a" is a strong signal of composition — should not short-circuit.
    assert is_fast_path("write a report on climate change") is False


def test_long_prompt_blocks_fast_path():
    # Prompt > 400 chars
    long = "x " * 250
    assert is_fast_path(long) is False


def test_multi_question_blocks_fast_path():
    # 3 sentence terminators — suggests composition, not trivia.
    assert (
        is_fast_path("What is A? What is B? What is C? What is D?") is False
    )


def test_empty_string_not_fast_path():
    assert is_fast_path("") is False


def test_non_string_not_fast_path():
    assert is_fast_path(None) is False  # type: ignore[arg-type]


# ── Keyword match is case-insensitive ──

def test_keyword_case_insensitive():
    assert is_fast_path("Research the market") is False
    assert is_fast_path("ANALYZE this") is False


# ── Positive short-circuit for factual-question prefixes ──

def test_capital_of_is_fast_path():
    assert is_fast_path("what's the capital of Peru") is True


def test_who_is_is_fast_path():
    assert is_fast_path("who is marie curie") is True


def test_when_did_is_fast_path():
    assert is_fast_path("when did ww2 start?") is True


def test_how_many_is_fast_path():
    assert is_fast_path("how many planets are in the solar system?") is True


def test_define_prefix_is_fast_path():
    assert is_fast_path("definition of osmosis") is True


def test_analyze_still_blocks_fast_path():
    # Regression: positive prefixes don't override the no-research-keyword rule
    # when the prompt isn't a factual-prefix shape.
    assert is_fast_path("analyze X") is False


def test_research_keyword_wins_when_no_factual_prefix():
    # "research the population of Tokyo" — doesn't start with a factual prefix,
    # so the research keyword still wins and blocks fast-path.
    assert is_fast_path("research the population of Tokyo") is False


def test_current_data_blocks_factual_prefix_fast_path():
    assert is_fast_path("what is the latest price of Apple stock?") is False
    assert is_fast_path("who is the current CEO of OpenAI?") is False


def test_recommendation_and_model_selection_block_fast_path():
    assert is_fast_path("what's the best laptop to buy this month?") is False
    assert is_fast_path("which model should I use, qwen or gpt-oss?") is False


def test_citation_and_debugging_prompts_block_fast_path():
    assert is_fast_path("find citations for local model latency") is False
    assert is_fast_path("debug why the macOS app is stuck launching") is False
