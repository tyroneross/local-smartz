"""Tests for the plugin validator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from localsmartz.plugins.validator import (
    parse_frontmatter,
    validate_command_md,
    validate_hooks_json,
    validate_mcp_json,
    validate_plugin,
    validate_plugin_json,
    validate_skill_md,
)


REAL_BOOKMARK = Path(
    "/Users/tyroneross/Desktop/git-folder/RossLabs-AI-Toolkit/plugins/bookmark"
)
REAL_PLUGIN_SYNC = Path(
    "/Users/tyroneross/Desktop/git-folder/RossLabs-AI-Toolkit/plugins/plugin-sync"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _codes(report) -> list[str]:
    return [i.code for i in report.issues]


def _write_plugin(
    root: Path,
    *,
    plugin_json: dict | None = None,
    with_skill: bool = False,
    skill_name: str = "example-skill",
    skill_description: str | None = None,
    with_command: bool = False,
    hooks: dict | None = None,
    mcp: dict | None = None,
) -> Path:
    plugin_dir = root / "my-plugin"
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    meta = plugin_json or {
        "name": "my-plugin",
        "version": "1.0.0",
        "description": "A well-described plugin for testing.",
        "author": {"name": "Tester"},
    }
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(json.dumps(meta))

    if with_skill:
        skill_dir = plugin_dir / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        desc = skill_description or (
            "This skill activates when the user wants to test the validator. "
            "Use it during automated tests to exercise frontmatter parsing paths."
        )
        body = "# Skill body\n\n" + ("x" * 150)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: {desc}\nversion: 1.0.0\n---\n{body}\n"
        )

    if with_command:
        cmd_dir = plugin_dir / "commands"
        cmd_dir.mkdir(parents=True)
        (cmd_dir / "do-thing.md").write_text(
            '---\ndescription: "Does a thing"\nallowed-tools: Bash, Write\n---\nRun ls.\n'
        )

    if hooks is not None:
        (plugin_dir / "hooks").mkdir(parents=True)
        (plugin_dir / "hooks" / "hooks.json").write_text(json.dumps(hooks))

    if mcp is not None:
        (plugin_dir / ".mcp.json").write_text(json.dumps(mcp))

    return plugin_dir


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------


def test_parse_frontmatter_basic():
    data, body, err = parse_frontmatter("---\nname: foo\ndesc: hi\n---\nbody")
    assert err is None
    assert data == {"name": "foo", "desc": "hi"}
    assert body == "body"


def test_parse_frontmatter_missing_close():
    data, body, err = parse_frontmatter("---\nname: foo\nstill going")
    assert data is None
    assert err is not None and "closing" in err.lower()


def test_parse_frontmatter_no_frontmatter():
    data, body, err = parse_frontmatter("no frontmatter here")
    assert data is None and err is None


# ---------------------------------------------------------------------------
# plugin.json
# ---------------------------------------------------------------------------


def test_plugin_json_missing(tmp_path):
    r = validate_plugin_json(tmp_path / "plugin.json")
    assert not r.ok
    assert "MISSING_PLUGIN_JSON" in _codes(r)


def test_plugin_json_invalid_semver(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text(
        json.dumps(
            {
                "name": "good-name",
                "version": "not-semver",
                "description": "long enough description",
                "author": {"name": "n"},
            }
        )
    )
    r = validate_plugin_json(p)
    assert "INVALID_SEMVER" in _codes(r)
    assert not r.ok


def test_plugin_json_missing_name(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text(
        json.dumps(
            {"version": "1.0.0", "description": "x" * 20, "author": {"name": "n"}}
        )
    )
    r = validate_plugin_json(p)
    assert "MISSING_NAME" in _codes(r)


def test_plugin_json_ok(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text(
        json.dumps(
            {
                "name": "ok-name",
                "version": "0.1.0",
                "description": "This is a fine description",
                "author": {"name": "Someone"},
            }
        )
    )
    r = validate_plugin_json(p)
    assert r.ok
    assert not any(i.severity == "error" for i in r.issues)


# ---------------------------------------------------------------------------
# SKILL.md
# ---------------------------------------------------------------------------


def test_skill_md_missing_frontmatter(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("just text")
    r = validate_skill_md(p)
    assert "MISSING_FRONTMATTER" in _codes(r)


def test_skill_md_malformed_yaml(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text("---\nname: foo\nno-close")
    r = validate_skill_md(p)
    assert "MALFORMED_YAML" in _codes(r)


def test_skill_md_invalid_name(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text(
        "---\nname: Bad_Name\ndescription: "
        + "x" * 100
        + "\n---\n"
        + ("body " * 50)
    )
    r = validate_skill_md(p)
    assert "INVALID_NAME" in _codes(r)


def test_skill_md_body_too_short(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text(
        "---\nname: ok-name\ndescription: " + "x" * 100 + "\n---\nshort"
    )
    r = validate_skill_md(p)
    assert "SKILL_BODY_TOO_SHORT" in _codes(r)


def test_skill_md_ok(tmp_path):
    p = tmp_path / "SKILL.md"
    p.write_text(
        "---\nname: ok-name\ndescription: "
        + ("x" * 120)
        + "\n---\n"
        + ("body text " * 50)
    )
    r = validate_skill_md(p)
    assert r.ok


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def test_command_missing_allowed_tools(tmp_path):
    p = tmp_path / "cmd.md"
    p.write_text('---\ndescription: "x"\n---\nbody')
    r = validate_command_md(p)
    assert "MISSING_ALLOWED_TOOLS" in _codes(r)


def test_command_ok(tmp_path):
    p = tmp_path / "cmd.md"
    p.write_text('---\ndescription: "x"\nallowed-tools: Bash, Write\n---\nbody')
    r = validate_command_md(p)
    assert r.ok


# ---------------------------------------------------------------------------
# hooks.json
# ---------------------------------------------------------------------------


def test_hooks_invalid_timeout(tmp_path):
    p = tmp_path / "hooks.json"
    p.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "",
                            "hooks": [
                                {"type": "command", "command": "x", "timeout": -1}
                            ],
                        }
                    ]
                }
            }
        )
    )
    r = validate_hooks_json(p)
    assert "INVALID_TIMEOUT" in _codes(r)


def test_hooks_ok(tmp_path):
    p = tmp_path / "hooks.json"
    p.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "",
                            "hooks": [
                                {"type": "command", "command": "x", "timeout": 10000}
                            ],
                        }
                    ]
                }
            }
        )
    )
    r = validate_hooks_json(p)
    assert r.ok


# ---------------------------------------------------------------------------
# .mcp.json
# ---------------------------------------------------------------------------


def test_mcp_missing_command(tmp_path):
    p = tmp_path / ".mcp.json"
    p.write_text(json.dumps({"srv": {"args": ["a"]}}))
    r = validate_mcp_json(p)
    assert "INVALID_MCP_COMMAND" in _codes(r)


def test_mcp_ok(tmp_path):
    p = tmp_path / ".mcp.json"
    p.write_text(
        json.dumps(
            {"srv": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/s.js"]}}
        )
    )
    r = validate_mcp_json(p)
    assert r.ok


# ---------------------------------------------------------------------------
# Whole-plugin
# ---------------------------------------------------------------------------


def test_validate_synthesized_plugin(tmp_path):
    plugin_dir = _write_plugin(
        tmp_path,
        with_skill=True,
        with_command=True,
        hooks={
            "hooks": {
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "echo hi", "timeout": 1000}
                        ],
                    }
                ]
            }
        },
        mcp={"srv": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/x.js"]}},
    )
    r = validate_plugin(plugin_dir)
    assert r.ok, [i.__dict__ for i in r.issues if i.severity == "error"]


@pytest.mark.skipif(not REAL_BOOKMARK.is_dir(), reason="real plugin not available")
def test_validate_real_bookmark():
    r = validate_plugin(REAL_BOOKMARK)
    assert r.ok, [i.__dict__ for i in r.issues if i.severity == "error"]


@pytest.mark.skipif(not REAL_PLUGIN_SYNC.is_dir(), reason="real plugin not available")
def test_validate_real_plugin_sync():
    r = validate_plugin(REAL_PLUGIN_SYNC)
    assert r.ok, [i.__dict__ for i in r.issues if i.severity == "error"]
