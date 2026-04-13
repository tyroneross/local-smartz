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
