"""Tests that the single/primary agent system prompt contains the PowerPoint policy."""

import pytest

from localsmartz.agent import _build_system_prompt, SYSTEM_PROMPT_FULL, SYSTEM_PROMPT_LITE


def _minimal_profile(name: str) -> dict:
    """Return a minimal profile dict sufficient for _build_system_prompt."""
    return {"name": name}


class TestPowerPointPolicyFull:
    """SYSTEM_PROMPT_FULL — used by the full-RAM / standard profile path."""

    def test_contains_powerpoint_keyword(self):
        assert "PowerPoint" in SYSTEM_PROMPT_FULL

    def test_mentions_markdown_outline(self):
        assert "markdown" in SYSTEM_PROMPT_FULL.lower()

    def test_mentions_slide_deck(self):
        assert "slide deck" in SYSTEM_PROMPT_FULL

    def test_rendered_via_build_system_prompt(self):
        profile = _minimal_profile("full")
        rendered = _build_system_prompt(
            profile,
            include_plugin_skills=False,
        )
        assert "PowerPoint" in rendered
        assert "markdown" in rendered.lower()


class TestPowerPointPolicyLite:
    """SYSTEM_PROMPT_LITE — used by lite/mini-RAM profile path."""

    def test_contains_powerpoint_keyword(self):
        assert "PowerPoint" in SYSTEM_PROMPT_LITE

    def test_mentions_markdown_outline(self):
        assert "markdown" in SYSTEM_PROMPT_LITE.lower()

    def test_mentions_slide_deck(self):
        assert "slide deck" in SYSTEM_PROMPT_LITE

    def test_rendered_via_build_system_prompt(self):
        profile = _minimal_profile("lite")
        rendered = _build_system_prompt(
            profile,
            include_plugin_skills=False,
        )
        assert "PowerPoint" in rendered
        assert "markdown" in rendered.lower()
