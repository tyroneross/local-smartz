"""Tests for the tool call validation middleware."""

import json

import pytest

from localsmartz.validation import (
    normalize_args,
    validate_tool_call,
    LoopDetector,
    check_output_quality,
    _suggest_tool,
)


# ── normalize_args ──

class TestNormalizeArgs:
    def test_dict_passthrough(self):
        args = {"query": "test"}
        assert normalize_args(args) == {"query": "test"}

    def test_json_string_parsed(self):
        args = '{"query": "test", "max_results": 5}'
        result = normalize_args(args)
        assert result == {"query": "test", "max_results": 5}

    def test_invalid_json_returns_empty(self):
        result = normalize_args("not json at all")
        assert result == {}

    def test_json_array_returns_empty(self):
        """JSON arrays are not valid tool args — should return empty dict."""
        result = normalize_args('[1, 2, 3]')
        assert result == {}

    def test_none_returns_empty(self):
        result = normalize_args(None)
        assert result == {}

    def test_empty_dict(self):
        assert normalize_args({}) == {}


# ── validate_tool_call ──

class TestValidateToolCall:
    AVAILABLE = ["web_search", "scrape_url", "python_exec", "create_report", "read_text_file"]

    def test_valid_call(self):
        tc = {"name": "web_search", "args": {"query": "test"}}
        ok, err, args = validate_tool_call(tc, self.AVAILABLE)
        assert ok is True
        assert err == ""
        assert args == {"query": "test"}

    def test_missing_name(self):
        tc = {"args": {"query": "test"}}
        ok, err, args = validate_tool_call(tc, self.AVAILABLE)
        assert ok is False
        assert "missing" in err.lower()

    def test_unknown_tool(self):
        tc = {"name": "google_search", "args": {"query": "test"}}
        ok, err, args = validate_tool_call(tc, self.AVAILABLE)
        assert ok is False
        assert "does not exist" in err

    def test_unknown_tool_with_suggestion(self):
        tc = {"name": "search", "args": {"query": "test"}}
        ok, err, args = validate_tool_call(tc, self.AVAILABLE)
        assert ok is False
        assert "web_search" in err

    def test_stringified_json_args(self):
        tc = {"name": "web_search", "args": '{"query": "test"}'}
        ok, err, args = validate_tool_call(tc, self.AVAILABLE)
        assert ok is True
        assert args == {"query": "test"}

    def test_missing_required_param(self):
        tc = {"name": "web_search", "args": {"max_results": 5}}
        ok, err, args = validate_tool_call(tc, self.AVAILABLE)
        assert ok is False
        assert "query" in err

    def test_wrong_type_param(self):
        tc = {"name": "web_search", "args": {"query": 123}}
        ok, err, args = validate_tool_call(tc, self.AVAILABLE)
        assert ok is False
        assert "str" in err

    def test_optional_param_missing_ok(self):
        tc = {"name": "web_search", "args": {"query": "test"}}
        ok, err, args = validate_tool_call(tc, self.AVAILABLE)
        assert ok is True

    def test_list_as_json_string_accepted(self):
        """create_report sections can be a JSON string — should be parsed to list."""
        sections = json.dumps([{"heading": "Test", "content": "Body"}])
        tc = {
            "name": "create_report",
            "args": {"title": "Test", "sections": sections, "output_path": "/tmp/test.md"},
        }
        ok, err, args = validate_tool_call(tc, ["create_report"])
        assert ok is True
        assert isinstance(args["sections"], list)

    def test_unknown_tool_not_in_schema(self):
        """Tools not in TOOL_SCHEMAS pass if they're in available_tools."""
        tc = {"name": "write_todos", "args": {}}
        ok, err, args = validate_tool_call(tc, ["write_todos"])
        assert ok is True


# ── _suggest_tool ──

class TestSuggestTool:
    AVAILABLE = ["web_search", "scrape_url", "python_exec", "create_report"]

    def test_search_alias(self):
        assert _suggest_tool("search", self.AVAILABLE) == "web_search"

    def test_browse_alias(self):
        assert _suggest_tool("browse", self.AVAILABLE) == "scrape_url"

    def test_python_alias(self):
        assert _suggest_tool("run_python", self.AVAILABLE) == "python_exec"

    def test_no_match(self):
        assert _suggest_tool("delete_everything", self.AVAILABLE) is None

    def test_substring_match(self):
        assert _suggest_tool("web_search_tool", self.AVAILABLE) == "web_search"


# ── LoopDetector ──

class TestLoopDetector:
    def test_no_loop_different_tools(self):
        ld = LoopDetector(max_repeats=3)
        assert ld.record("web_search") is False
        assert ld.record("scrape_url") is False
        assert ld.record("python_exec") is False

    def test_loop_detected(self):
        ld = LoopDetector(max_repeats=3)
        assert ld.record("web_search") is False
        assert ld.record("web_search") is False
        assert ld.record("web_search") is True  # 3rd consecutive

    def test_interleaved_no_loop(self):
        ld = LoopDetector(max_repeats=3)
        ld.record("web_search")
        ld.record("web_search")
        ld.record("scrape_url")  # breaks the chain
        ld.record("web_search")
        assert ld.record("web_search") is False  # only 2 consecutive

    def test_reset_clears_history(self):
        ld = LoopDetector(max_repeats=3)
        ld.record("web_search")
        ld.record("web_search")
        ld.reset()
        assert ld.record("web_search") is False
        assert ld.record("web_search") is False

    def test_last_tool(self):
        ld = LoopDetector()
        assert ld.last_tool is None
        ld.record("web_search")
        assert ld.last_tool == "web_search"

    def test_same_tool_different_args_no_loop(self):
        ld = LoopDetector(max_repeats=3)
        assert ld.record("web_search", {"query": "tokyo population"}) is False
        assert ld.record("web_search", {"query": "tokyo area"}) is False
        assert ld.record("web_search", {"query": "tokyo economy"}) is False
        # Different args each time — not a loop

    def test_same_tool_same_args_loop(self):
        ld = LoopDetector(max_repeats=3)
        args = {"query": "same query"}
        assert ld.record("web_search", args) is False
        assert ld.record("web_search", args) is False
        assert ld.record("web_search", args) is True  # True loop

    def test_name_only_loop_after_max_name_repeats(self):
        ld = LoopDetector(max_repeats=3, max_name_repeats=5)
        assert ld.record("web_search", {"query": "q1"}) is False
        assert ld.record("web_search", {"query": "q2"}) is False
        assert ld.record("web_search", {"query": "q3"}) is False
        assert ld.record("web_search", {"query": "q4"}) is False
        assert ld.record("web_search", {"query": "q5"}) is True  # 5th name-only


# ── check_output_quality ──

class TestCheckOutputQuality:
    def test_empty_response(self):
        ok, issue = check_output_quality("", "test prompt")
        assert ok is False
        assert issue == "empty_response"

    def test_whitespace_only(self):
        ok, issue = check_output_quality("   \n  ", "test prompt")
        assert ok is False
        assert issue == "empty_response"

    def test_too_short(self):
        ok, issue = check_output_quality("Yes.", "What is AI?")
        assert ok is False
        assert issue == "too_short"

    def test_off_topic(self):
        ok, issue = check_output_quality(
            "The weather today is sunny and warm with clear skies expected.",
            "quantum computing",
        )
        assert ok is False
        assert issue == "off_topic"

    def test_acceptable_response(self):
        ok, issue = check_output_quality(
            "Quantum computing uses qubits to perform calculations exponentially faster than classical computers.",
            "What is quantum computing?",
        )
        assert ok is True
        assert issue == ""

    def test_custom_min_length(self):
        ok, issue = check_output_quality("The answer is yes", "answer", min_length=3)
        assert ok is True
