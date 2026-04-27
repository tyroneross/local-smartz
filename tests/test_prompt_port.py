"""Prompt .md port: orchestrator and fact_checker prompts must load from disk.

Verifies get_role_prompt() prefers the .md file over the in-dict string.
"""
from __future__ import annotations

from localsmartz import profiles
from localsmartz.agents.definitions import list_prompts, load_prompt


def test_orchestrator_and_fact_checker_md_exist() -> None:
    names = list_prompts()
    assert "orchestrator" in names
    assert "fact_checker" in names


def test_orchestrator_prompt_keeps_anti_hallucination_guardrail() -> None:
    body = load_prompt("orchestrator")
    assert "Never invent tool namespaces" in body
    assert "orchestrator" in body.lower()


def test_fact_checker_prompt_returns_json_shape() -> None:
    body = load_prompt("fact_checker")
    assert "verdict" in body
    # Must document needs_more / ok verdicts.
    assert "needs_more" in body
    assert "\"ok\"" in body or "'ok'" in body


def test_get_role_prompt_reads_md_first() -> None:
    body = profiles.get_role_prompt("orchestrator")
    assert body
    assert "ORCHESTRATOR" in body or "orchestrator" in body.lower()


def test_get_role_prompt_falls_back_to_dict_for_unported_roles() -> None:
    # researcher has an existing .md (pre-existing) — must still return a string.
    body = profiles.get_role_prompt("researcher")
    assert isinstance(body, str) and body
