"""Tests for user intent retention across tool errors (deliverable 1c).

IntentAnchor:
- Persists the original user prompt at instantiation.
- Tracks tool error count via record_error().
- Provides a recovery_message() referencing the original prompt.
- should_inject() returns True only after at least one error.

Integration: the serve.py observation loop calls intent_anchor.record_error()
on every tool result message whose content starts with "Error", then emits
an ``intent_recovery`` SSE event containing the recovery message. This keeps
the original task visible so the model doesn't fall back to a generic prompt.
"""
from __future__ import annotations

import pytest

from localsmartz.validation import IntentAnchor


class TestIntentAnchorState:
    """Unit contract for IntentAnchor."""

    def test_stores_original_prompt(self):
        anchor = IntentAnchor("draft a PowerPoint on Arista vs Juniper")
        assert anchor.original_prompt == "draft a PowerPoint on Arista vs Juniper"

    def test_initial_error_count_is_zero(self):
        anchor = IntentAnchor("some task")
        assert anchor.error_count == 0

    def test_should_inject_false_before_any_error(self):
        anchor = IntentAnchor("some task")
        assert anchor.should_inject() is False

    def test_should_inject_true_after_first_error(self):
        anchor = IntentAnchor("some task")
        anchor.record_error()
        assert anchor.should_inject() is True

    def test_error_count_increments(self):
        anchor = IntentAnchor("some task")
        anchor.record_error()
        anchor.record_error()
        anchor.record_error()
        assert anchor.error_count == 3


class TestIntentAnchorRecoveryMessage:
    """Recovery message must contain the original prompt verbatim."""

    def test_recovery_message_contains_original_prompt(self):
        task = "draft a PowerPoint on Arista vs Juniper"
        anchor = IntentAnchor(task)
        msg = anchor.recovery_message()
        assert task in msg

    def test_recovery_message_mentions_tool_call_failed(self):
        anchor = IntentAnchor("research quantum computing")
        msg = anchor.recovery_message()
        assert "Tool call failed" in msg

    def test_recovery_message_instructs_to_retry_or_respond(self):
        anchor = IntentAnchor("research quantum computing")
        msg = anchor.recovery_message()
        # Must instruct the model to try again or respond directly —
        # not just emit an error.
        assert "Try again" in msg or "respond directly" in msg

    def test_recovery_message_is_string(self):
        anchor = IntentAnchor("any task")
        assert isinstance(anchor.recovery_message(), str)

    def test_recovery_message_stable_across_calls(self):
        """Same prompt → same message every time (deterministic)."""
        anchor = IntentAnchor("stable task")
        assert anchor.recovery_message() == anchor.recovery_message()


class TestIntentAnchorIntegration:
    """Simulate the serve.py observation loop's use of IntentAnchor.

    We do NOT spin up a live agent. We replicate the exact decision path
    from _run_full_agent: check msg.type == 'tool', check content.startswith('Error'),
    call record_error(), emit intent_recovery event.
    """

    def _make_tool_msg(self, content: str, name: str = "web_search"):
        """Minimal stand-in for a LangGraph ToolMessage."""
        class _FakeToolMsg:
            type = "tool"
        msg = _FakeToolMsg()
        msg.content = content
        msg.name = name
        return msg

    def test_no_intent_recovery_on_successful_tool_result(self):
        anchor = IntentAnchor("draft a PowerPoint on Arista vs Juniper")
        events: list[dict] = []

        msg = self._make_tool_msg("Here are the results: ...")
        content = msg.content
        is_error = content.startswith("Error")
        if is_error:
            anchor.record_error()
            events.append({"type": "intent_recovery", "message": anchor.recovery_message()})

        assert not events
        assert anchor.error_count == 0
        assert anchor.should_inject() is False

    def test_intent_recovery_emitted_on_tool_error(self):
        anchor = IntentAnchor("draft a PowerPoint on Arista vs Juniper")
        events: list[dict] = []

        msg = self._make_tool_msg("Error: tool 'definitely_not_a_real_tool' not found")
        content = msg.content
        is_error = content.startswith("Error")
        if is_error:
            anchor.record_error()
            events.append({"type": "intent_recovery", "message": anchor.recovery_message()})

        assert len(events) == 1
        ev = events[0]
        assert ev["type"] == "intent_recovery"
        assert "draft a PowerPoint on Arista vs Juniper" in ev["message"]

    def test_original_task_not_lost_after_invalid_tool(self):
        """The critical regression: after a failed tool call, the context
        injected must reference the original task, not a generic fallback."""
        original_task = "draft a PowerPoint on Arista vs Juniper"
        anchor = IntentAnchor(original_task)

        # Simulate: model calls nonexistent tool → error result
        error_content = "Error: tool 'definitely_not_a_real_tool' not found"
        if error_content.startswith("Error"):
            anchor.record_error()

        recovery = anchor.recovery_message()

        # The recovery message must reference the original task
        assert original_task in recovery
        # It must NOT be a generic greeting
        assert "What would you like me to help you with" not in recovery
        assert "How can I help" not in recovery

    def test_multiple_errors_accumulate_but_message_stays_anchored(self):
        task = "summarize the latest GDPR enforcement cases"
        anchor = IntentAnchor(task)
        for _ in range(3):
            anchor.record_error()

        assert anchor.error_count == 3
        # Message still references the original task regardless of error count
        assert task in anchor.recovery_message()

    def test_prompt_survives_different_tool_names(self):
        """Even when different bad tools are called, prompt stays anchored."""
        task = "compare AWS and GCP pricing"
        anchor = IntentAnchor(task)

        for bad_tool in ("bad_tool_1", "bad_tool_2", "also_bad"):
            error = f"Error: tool '{bad_tool}' not found"
            if error.startswith("Error"):
                anchor.record_error()

        assert task in anchor.recovery_message()
        assert anchor.error_count == 3
