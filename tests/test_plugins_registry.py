"""Tests for the plugin Registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from localsmartz.plugins import (
    Plugin,
    Registry,
    Skill,
    ValidationError,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect HOME so active_skills.json and default root land in tmp."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # pathlib.Path.home() reads HOME on POSIX
    return tmp_path


def _write_plugin(
    root: Path,
    name: str = "demo-plugin",
    with_skill: bool = True,
) -> Path:
    plugin_dir = root / name
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "A demo plugin used in tests.",
                "author": {"name": "Tester"},
            }
        )
    )
    if with_skill:
        skill_dir = plugin_dir / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: demo-skill\n"
            "description: This demo skill activates for registry tests and provides a long description so we pass length warnings.\n"
            "version: 1.0.0\n"
            "---\n"
            + ("body content line\n" * 20)
        )
    return plugin_dir


def test_registry_creates_root(tmp_path):
    root = tmp_path / "plugins"
    Registry(root)
    assert root.is_dir()


def test_install_symlink(tmp_path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    plugin_src = _write_plugin(src_root, name="demo-plugin")

    registry_root = tmp_path / "plugins"
    reg = Registry(registry_root)
    plugin = reg.install(plugin_src)

    assert isinstance(plugin, Plugin)
    assert plugin.name == "demo-plugin"
    assert (registry_root / "demo-plugin").is_symlink()

    listed = reg.list_plugins()
    assert [p.name for p in listed] == ["demo-plugin"]
    skills = reg.list_skills()
    assert any(s.name == "demo-skill" for s in skills)


def test_install_copy(tmp_path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    plugin_src = _write_plugin(src_root, name="copy-plugin")
    reg = Registry(tmp_path / "plugins")
    plugin = reg.install(plugin_src, copy=True)
    assert plugin.name == "copy-plugin"
    dest = tmp_path / "plugins" / "copy-plugin"
    assert dest.is_dir() and not dest.is_symlink()


def test_install_validates(tmp_path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    bad = src_root / "bad"
    (bad / ".claude-plugin").mkdir(parents=True)
    (bad / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "bad", "version": "NOPE", "description": "a description", "author": {"name": "x"}})
    )
    reg = Registry(tmp_path / "plugins")
    with pytest.raises(ValidationError):
        reg.install(bad)


def test_remove(tmp_path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    plugin_src = _write_plugin(src_root, name="gone")
    reg = Registry(tmp_path / "plugins")
    reg.install(plugin_src)
    assert reg.list_plugins()
    reg.remove("gone")
    assert not reg.list_plugins()


def test_active_skills_roundtrip(fake_home):
    reg = Registry(fake_home / "plugins")
    assert reg.get_active_skill_names() == []
    reg.set_active_skill_names(["one", "two", "one"])
    assert reg.get_active_skill_names() == ["one", "two"]


def test_list_skills_active_only(fake_home, tmp_path):
    src_root = tmp_path / "src"
    src_root.mkdir()
    _write_plugin(src_root, name="p1")
    reg = Registry(fake_home / "plugins")
    reg.install(src_root / "p1")
    # no active skills set -> empty
    assert reg.list_skills(active_only=True) == []
    reg.set_active_skill_names(["demo-skill"])
    active = reg.list_skills(active_only=True)
    assert [s.name for s in active] == ["demo-skill"]


def test_standalone_skill_discovered(tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    skill_dir = root / "solo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: solo-skill\ndescription: " + ("x" * 100)
        + "\n---\n" + ("body " * 50)
    )
    reg = Registry(root)
    skills = reg.list_skills()
    names = [s.name for s in skills]
    assert "solo-skill" in names
    # standalone skills should have plugin_name == None
    solo = [s for s in skills if s.name == "solo-skill"][0]
    assert solo.plugin_name is None


def test_validate_on_real_bookmark():
    real = Path(
        "/Users/tyroneross/Desktop/git-folder/RossLabs-AI-Toolkit/plugins/bookmark"
    )
    if not real.is_dir():
        pytest.skip("real plugin not available")
    reg = Registry(Path("/tmp/lsz-registry-smoke"))
    report = reg.validate(real)
    errs = [i for i in report.issues if i.severity == "error"]
    assert report.ok, errs
