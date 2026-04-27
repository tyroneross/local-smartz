"""Tests for the Harmony-format token parser (gpt-oss family models).

Fixture strings are the literal formats emitted by gpt-oss:20b.
"""

import pytest

from localsmartz.runners.harmony import strip_harmony_tokens, extract_harmony_tool_call


# ---------------------------------------------------------------------------
# strip_harmony_tokens
# ---------------------------------------------------------------------------

class TestStripHarmonyTokens:
    def test_plain_name_unchanged(self):
        """A plain tool name with no harmony markers is returned as-is."""
        assert strip_harmony_tokens("web_search") == "web_search"

    def test_bare_wrapper_commentary(self):
        """Bare wrapper with commentary channel extracts the tool name."""
        raw = "assistant<|channel|>commentary<|message|>functions=web_search"
        assert strip_harmony_tokens(raw) == "web_search"

    def test_full_call_form_returns_name_only(self):
        """Full call form (with args) still returns just the name."""
        raw = 'assistant<|channel|>commentary<|message|>functions=web_search(query="foo", max_results=10)'
        assert strip_harmony_tokens(raw) == "web_search"

    def test_channel_analysis(self):
        """analysis channel works identically to commentary."""
        raw = "assistant<|channel|>analysis<|message|>functions=read_file(path=\"/tmp/x\")"
        assert strip_harmony_tokens(raw) == "read_file"

    def test_channel_final(self):
        """final channel works identically to commentary."""
        raw = "assistant<|channel|>final<|message|>functions=write_file"
        assert strip_harmony_tokens(raw) == "write_file"

    def test_no_markers_returns_unchanged(self):
        """String without any <| markers is returned unchanged."""
        assert strip_harmony_tokens("some_tool_name") == "some_tool_name"

    def test_empty_string(self):
        """Empty string is returned unchanged."""
        assert strip_harmony_tokens("") == ""


# ---------------------------------------------------------------------------
# extract_harmony_tool_call
# ---------------------------------------------------------------------------

class TestExtractHarmonyToolCall:
    def test_no_markers_returns_none(self):
        """String without harmony markers returns None."""
        assert extract_harmony_tool_call("web_search") is None

    def test_full_call_commentary(self):
        """Literal failure case from user session — commentary channel."""
        raw = 'assistant<|channel|>commentary<|message|>functions=web_search(query="foo", max_results=10)'
        result = extract_harmony_tool_call(raw)
        assert result == ("web_search", 'query="foo", max_results=10')

    def test_full_call_analysis_read_file(self):
        """analysis channel with read_file tool."""
        raw = 'assistant<|channel|>analysis<|message|>functions=read_file(path="/etc/hosts")'
        result = extract_harmony_tool_call(raw)
        assert result == ("read_file", 'path="/etc/hosts"')

    def test_full_call_final_write_file(self):
        """final channel with write_file tool."""
        raw = 'assistant<|channel|>final<|message|>functions=write_file(path="/out.txt", content="hello")'
        result = extract_harmony_tool_call(raw)
        assert result == ("write_file", 'path="/out.txt", content="hello"')

    def test_bare_wrapper_no_args_returns_none(self):
        """Bare wrapper (no parens / no args) returns None — no args to recover."""
        raw = "assistant<|channel|>commentary<|message|>functions=web_search"
        assert extract_harmony_tool_call(raw) is None

    def test_multiline_content_with_markers(self):
        """Multi-line content with harmony markers still extracts name and args."""
        raw = (
            "Thinking step by step...\n"
            "assistant<|channel|>commentary<|message|>"
            'functions=web_search(query="Arista vs Juniper", max_results=5)\n'
            "Some trailing text"
        )
        result = extract_harmony_tool_call(raw)
        assert result == ("web_search", 'query="Arista vs Juniper", max_results=5')

    def test_no_harmony_markers_anywhere_returns_none(self):
        """Confirm None for completely ordinary strings with no markers."""
        assert extract_harmony_tool_call("just_a_function_name(arg=1)") is None
