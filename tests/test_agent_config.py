"""Tests for profile-specific agent configuration (Phase 4)."""

from localsmartz.agent import (
    ALL_TOOLS,
    LITE_TOOLS,
    SYSTEM_PROMPT_FULL,
    SYSTEM_PROMPT_LITE,
)


# ── System prompt selection ──

def test_full_prompt_has_subagent_section():
    assert "task tool" in SYSTEM_PROMPT_FULL
    assert "subagent" in SYSTEM_PROMPT_FULL.lower()


def test_lite_prompt_no_subagent():
    assert "task tool" not in SYSTEM_PROMPT_LITE
    assert "subagent" not in SYSTEM_PROMPT_LITE.lower()


def test_lite_prompt_has_examples():
    assert "Example" in SYSTEM_PROMPT_LITE or "example" in SYSTEM_PROMPT_LITE.lower()


def test_lite_prompt_one_tool_per_turn():
    assert "ONE tool per turn" in SYSTEM_PROMPT_LITE


def test_lite_prompt_step_enforcement():
    assert "Step 1" in SYSTEM_PROMPT_LITE
    assert "Step 2" in SYSTEM_PROMPT_LITE


# ── Tool subsets ──

def test_all_tools_count():
    assert len(ALL_TOOLS) == 8


def test_lite_tools_count():
    assert len(LITE_TOOLS) == 5


def test_lite_tools_subset_of_all():
    all_names = {t.name for t in ALL_TOOLS}
    lite_names = {t.name for t in LITE_TOOLS}
    assert lite_names.issubset(all_names)


def test_lite_excludes_complex_tools():
    lite_names = {t.name for t in LITE_TOOLS}
    assert "parse_pdf" not in lite_names
    assert "read_spreadsheet" not in lite_names
    assert "create_spreadsheet" not in lite_names


def test_lite_includes_core_tools():
    lite_names = {t.name for t in LITE_TOOLS}
    assert "web_search" in lite_names
    assert "scrape_url" in lite_names
    assert "python_exec" in lite_names
    assert "create_report" in lite_names
    assert "read_text_file" in lite_names
