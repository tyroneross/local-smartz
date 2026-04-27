"""Tests for G1/G2/G5/G9 gaps in the plugin validator.

G1  — agent.md discovery + validation
G2  — plugin.json author optional (downgrade to warning)
G5  — SKILL.md name optional (downgrade to warning)
G9  — command allowed-tools optional (downgrade to warning)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from localsmartz.plugins.discovery import find_agent_files
from localsmartz.plugins.validator import (
    validate_agent_md,
    validate_plugin,
    validate_plugin_json,
    validate_skill_md,
    validate_command_md,
)


def _codes(report) -> list[str]:
    return [i.code for i in report.issues]


def _severities(report) -> dict[str, str]:
    return {i.code: i.severity for i in report.issues}


# ---------------------------------------------------------------------------
# G1 — agent.md validation
# ---------------------------------------------------------------------------


def test_validate_agent_md_minimal_valid(tmp_path):
    p = tmp_path / "my-agent.md"
    p.write_text("---\nname: my-agent\ndescription: Handles data ingestion tasks.\n---\nDo the thing.\n")
    r = validate_agent_md(p)
    assert r.ok, [i.__dict__ for i in r.issues if i.severity == "error"]
    assert not any(i.severity == "error" for i in r.issues)


def test_validate_agent_md_missing_name(tmp_path):
    p = tmp_path / "agent.md"
    p.write_text("---\ndescription: Does something useful.\n---\nSystem prompt here.\n")
    r = validate_agent_md(p)
    assert not r.ok
    assert "MISSING_NAME" in _codes(r)


def test_validate_agent_md_invalid_model_id(tmp_path):
    p = tmp_path / "agent.md"
    p.write_text(
        "---\nname: my-agent\ndescription: Handles tasks.\nmodel: gpt-4\n---\nDo things.\n"
    )
    r = validate_agent_md(p)
    assert not r.ok
    assert "INVALID_MODEL" in _codes(r)


def test_validate_agent_md_valid_model_alias(tmp_path):
    p = tmp_path / "agent.md"
    p.write_text(
        "---\nname: my-agent\ndescription: Handles tasks.\nmodel: sonnet\n---\nDo things.\n"
    )
    r = validate_agent_md(p)
    assert r.ok, [i.__dict__ for i in r.issues if i.severity == "error"]
    assert "INVALID_MODEL" not in _codes(r)


def test_validate_agent_md_valid_full_model_id(tmp_path):
    p = tmp_path / "agent.md"
    p.write_text(
        "---\nname: my-agent\ndescription: Handles tasks.\nmodel: claude-sonnet-4-6\n---\nDo things.\n"
    )
    r = validate_agent_md(p)
    assert r.ok, [i.__dict__ for i in r.issues if i.severity == "error"]
    assert "INVALID_MODEL" not in _codes(r)


def test_validate_agent_md_empty_body(tmp_path):
    p = tmp_path / "agent.md"
    p.write_text("---\nname: my-agent\ndescription: Handles tasks.\n---\n   \n")
    r = validate_agent_md(p)
    assert not r.ok
    assert "AGENT_BODY_EMPTY" in _codes(r)


def test_validate_agent_md_valid_inherit_model(tmp_path):
    p = tmp_path / "agent.md"
    p.write_text(
        "---\nname: my-agent\ndescription: Handles tasks.\nmodel: inherit\n---\nDo things.\n"
    )
    r = validate_agent_md(p)
    assert r.ok
    assert "INVALID_MODEL" not in _codes(r)


def test_validate_agent_md_maxturn_coercion(tmp_path):
    """maxTurns is returned as a string from parse_frontmatter; validator must coerce."""
    p = tmp_path / "agent.md"
    p.write_text(
        "---\nname: my-agent\ndescription: Handles tasks.\nmaxTurns: 20\n---\nDo things.\n"
    )
    r = validate_agent_md(p)
    assert r.ok
    assert "INVALID_MAX_TURNS" not in _codes(r)


# ---------------------------------------------------------------------------
# G1 — find_agent_files discovery
# ---------------------------------------------------------------------------


def test_find_agent_files_discovers_root_and_skill_nested(tmp_path):
    plugin_dir = tmp_path / "my-plugin"

    # Root-level agent
    (plugin_dir / "agents").mkdir(parents=True)
    (plugin_dir / "agents" / "a.md").write_text(
        "---\nname: agent-a\ndescription: Root agent.\n---\nSystem prompt.\n"
    )

    # Skill-nested agent
    (plugin_dir / "skills" / "x" / "agents").mkdir(parents=True)
    (plugin_dir / "skills" / "x" / "agents" / "b.md").write_text(
        "---\nname: agent-b\ndescription: Nested agent.\n---\nSystem prompt.\n"
    )

    found = find_agent_files(plugin_dir)
    names = {p.name for p in found}
    assert "a.md" in names
    assert "b.md" in names
    assert len(found) == 2


def test_find_agent_files_empty_when_no_agents(tmp_path):
    plugin_dir = tmp_path / "my-plugin"
    plugin_dir.mkdir()
    assert find_agent_files(plugin_dir) == []


# ---------------------------------------------------------------------------
# G1 — validate_plugin walks agents
# ---------------------------------------------------------------------------


def _make_minimal_plugin(root: Path) -> Path:
    """A fully valid plugin with plugin.json only."""
    plugin_dir = root / "my-plugin"
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "my-plugin",
                "version": "1.0.0",
                "description": "A well-described plugin for testing.",
                "author": {"name": "Tester"},
            }
        )
    )
    return plugin_dir


def test_validate_plugin_walks_agents_valid(tmp_path):
    plugin_dir = _make_minimal_plugin(tmp_path)

    # Add a valid skill
    skill_dir = plugin_dir / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: "
        + ("x" * 120)
        + "\n---\n"
        + ("body text " * 50)
        + "\n"
    )

    # Add a valid root-level agent
    (plugin_dir / "agents").mkdir(parents=True)
    (plugin_dir / "agents" / "helper.md").write_text(
        "---\nname: helper\ndescription: Helps with tasks.\n---\nSystem prompt.\n"
    )

    r = validate_plugin(plugin_dir)
    assert r.ok, [i.__dict__ for i in r.issues if i.severity == "error"]


def test_validate_plugin_walks_agents_invalid_agent_fails(tmp_path):
    plugin_dir = _make_minimal_plugin(tmp_path)

    # Add an agent missing the required 'description'
    (plugin_dir / "agents").mkdir(parents=True)
    (plugin_dir / "agents" / "bad-agent.md").write_text(
        "---\nname: bad-agent\n---\nSystem prompt.\n"
    )

    r = validate_plugin(plugin_dir)
    assert not r.ok
    assert "MISSING_DESCRIPTION" in _codes(r)


# ---------------------------------------------------------------------------
# G2 — plugin.json author optional (downgrade to warning)
# ---------------------------------------------------------------------------


def test_plugin_json_missing_author_is_warning(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text(
        json.dumps(
            {
                "name": "my-plugin",
                "version": "1.0.0",
                "description": "A well-described plugin.",
            }
        )
    )
    r = validate_plugin_json(p)
    assert r.ok, [i.__dict__ for i in r.issues if i.severity == "error"]
    codes = _codes(r)
    severities = _severities(r)
    assert "AUTHOR_RECOMMENDED" in codes
    assert severities["AUTHOR_RECOMMENDED"] == "warning"


def test_plugin_json_invalid_author_still_errors(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text(
        json.dumps(
            {
                "name": "my-plugin",
                "version": "1.0.0",
                "description": "A well-described plugin.",
                "author": {"email": "no-name@example.com"},
            }
        )
    )
    r = validate_plugin_json(p)
    assert not r.ok
    assert "INVALID_AUTHOR" in _codes(r)


# ---------------------------------------------------------------------------
# G5 — SKILL.md name optional (downgrade to warning)
# ---------------------------------------------------------------------------


def test_skill_md_missing_name_is_warning(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text(
        "---\ndescription: "
        + ("x" * 120)
        + "\n---\n"
        + ("body text " * 50)
        + "\n"
    )
    r = validate_skill_md(p)
    assert r.ok, [i.__dict__ for i in r.issues if i.severity == "error"]
    codes = _codes(r)
    severities = _severities(r)
    assert "NAME_RECOMMENDED" in codes
    assert severities["NAME_RECOMMENDED"] == "warning"


# ---------------------------------------------------------------------------
# G9 — command allowed-tools optional (downgrade to warning)
# ---------------------------------------------------------------------------


def test_command_missing_allowed_tools_is_warning(tmp_path):
    p = tmp_path / "cmd.md"
    p.write_text('---\ndescription: "Does a thing"\n---\nRun the task.\n')
    r = validate_command_md(p)
    assert r.ok, [i.__dict__ for i in r.issues if i.severity == "error"]
    codes = _codes(r)
    severities = _severities(r)
    assert "ALLOWED_TOOLS_RECOMMENDED" in codes
    assert severities["ALLOWED_TOOLS_RECOMMENDED"] == "warning"
