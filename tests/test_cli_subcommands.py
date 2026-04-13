"""End-to-end tests for the noun-based CLI layer (`localsmartz plugins|skills|config`).

These tests exercise `python -m localsmartz <noun> ...` via subprocess so that
argparse wiring, exit codes, and stdout/stderr routing all get verified.
HOME is redirected into a tmp_path so nothing touches the real user home.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REAL_BOOKMARK_PATH = Path(
    "/Users/tyroneross/Desktop/git-folder/RossLabs-AI-Toolkit/plugins/bookmark"
)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect HOME/USERPROFILE so CLI state lands in tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def _run_cli(isolated_home: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(isolated_home)
    env["USERPROFILE"] = str(isolated_home)
    # Make sure the src layout is importable.
    repo_root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(repo_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "localsmartz", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"CLI failed ({proc.returncode}): stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )
    return proc


def _write_minimal_plugin(root: Path, name: str = "demo-plugin") -> Path:
    plugin_dir = root / name
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "A demo plugin used in CLI tests.",
                "author": {"name": "Tester"},
            }
        )
    )
    skill_dir = plugin_dir / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: This demo skill activates for CLI tests and has a sufficiently long description.\n"
        "version: 1.0.0\n"
        "---\n"
        + ("skill body line\n" * 20)
    )
    return plugin_dir


# ---------------------------------------------------------------------------
# Legacy flags still work (sanity)
# ---------------------------------------------------------------------------


def test_version_flag_still_works(isolated_home):
    r = _run_cli(isolated_home, "--version")
    assert r.returncode == 0
    assert "0.1.0" in r.stdout


def test_help_flag_still_works(isolated_home):
    r = _run_cli(isolated_home, "--help")
    assert r.returncode == 0
    assert "localsmartz" in r.stdout.lower() or "prompt" in r.stdout.lower()


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def test_config_get_defaults_on_fresh_home(isolated_home):
    r = _run_cli(isolated_home, "config", "get", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert set(data.keys()) >= {
        "workspace",
        "python_path",
        "active_model",
        "plugin_paths",
        "active_skills",
    }
    for key, entry in data.items():
        assert entry["source"] == "default"
    # Getting without file should not create the file.
    assert not (isolated_home / ".localsmartz" / "global.json").exists()


def test_config_set_and_get_roundtrip(isolated_home):
    r = _run_cli(isolated_home, "config", "set", "workspace", "/tmp/foo")
    assert r.returncode == 0, r.stderr
    r2 = _run_cli(isolated_home, "config", "get", "workspace")
    assert r2.returncode == 0
    assert r2.stdout.strip() == "/tmp/foo"


def test_config_set_list_accepts_comma_separated(isolated_home):
    r = _run_cli(isolated_home, "config", "set", "active_skills", "alpha,beta,gamma")
    assert r.returncode == 0, r.stderr
    r2 = _run_cli(isolated_home, "config", "get", "active_skills", "--json")
    assert r2.returncode == 0
    data = json.loads(r2.stdout)
    assert data["active_skills"] == ["alpha", "beta", "gamma"]


def test_config_set_wrong_type_exits_nonzero(isolated_home):
    # workspace must be a string path, not numeric.
    r = _run_cli(isolated_home, "config", "set", "workspace", "123")
    assert r.returncode != 0
    assert "error" in r.stderr.lower()


def test_config_set_unknown_key_exits_nonzero(isolated_home):
    r = _run_cli(isolated_home, "config", "set", "nonesuch", "whatever")
    assert r.returncode != 0
    assert "unknown" in r.stderr.lower() or "error" in r.stderr.lower()


def test_config_get_shows_source_file_after_set(isolated_home):
    _run_cli(isolated_home, "config", "set", "workspace", "/tmp/bar", check=True)
    r = _run_cli(isolated_home, "config", "get", "--json")
    data = json.loads(r.stdout)
    assert data["workspace"]["source"] == "file"
    assert data["workspace"]["value"] == "/tmp/bar"
    # Other keys still default.
    assert data["active_model"]["source"] == "default"


def test_config_reset_removes_file(isolated_home):
    _run_cli(isolated_home, "config", "set", "workspace", "/tmp/zap", check=True)
    assert (isolated_home / ".localsmartz" / "global.json").exists()
    r = _run_cli(isolated_home, "config", "reset")
    assert r.returncode == 0
    assert not (isolated_home / ".localsmartz" / "global.json").exists()


# ---------------------------------------------------------------------------
# plugins
# ---------------------------------------------------------------------------


def test_plugins_list_empty(isolated_home):
    r = _run_cli(isolated_home, "plugins", "list", "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data == []


def test_plugins_install_and_list(isolated_home, tmp_path):
    plugin_src = _write_minimal_plugin(tmp_path)
    r = _run_cli(isolated_home, "plugins", "install", str(plugin_src))
    assert r.returncode == 0, r.stderr
    assert "Installed" in r.stdout

    r2 = _run_cli(isolated_home, "plugins", "list", "--json")
    assert r2.returncode == 0
    data = json.loads(r2.stdout)
    names = [p["name"] for p in data]
    assert "demo-plugin" in names


def test_plugins_install_invalid_path_exits_nonzero(isolated_home, tmp_path):
    bad = tmp_path / "not-a-plugin"
    bad.mkdir()
    (bad / "random.txt").write_text("hello")
    r = _run_cli(isolated_home, "plugins", "install", str(bad))
    assert r.returncode != 0


def test_plugins_validate_bad_path_exits_one(isolated_home, tmp_path):
    bad = tmp_path / "empty-dir"
    bad.mkdir()
    r = _run_cli(isolated_home, "plugins", "validate", str(bad))
    assert r.returncode == 1


def test_plugins_validate_good_plugin_exits_zero(isolated_home, tmp_path):
    plugin_src = _write_minimal_plugin(tmp_path)
    r = _run_cli(isolated_home, "plugins", "validate", str(plugin_src))
    assert r.returncode == 0


def test_plugins_remove(isolated_home, tmp_path):
    plugin_src = _write_minimal_plugin(tmp_path, name="to-remove")
    _run_cli(isolated_home, "plugins", "install", str(plugin_src), check=True)
    r = _run_cli(isolated_home, "plugins", "remove", "to-remove")
    assert r.returncode == 0, r.stderr
    r2 = _run_cli(isolated_home, "plugins", "list", "--json")
    assert json.loads(r2.stdout) == []


def test_plugins_remove_unknown_exits_nonzero(isolated_home):
    r = _run_cli(isolated_home, "plugins", "remove", "nonesuch")
    assert r.returncode != 0


@pytest.mark.skipif(
    not REAL_BOOKMARK_PATH.exists(),
    reason="Real RossLabs-AI-Toolkit bookmark plugin not checked out",
)
def test_plugins_install_real_bookmark(isolated_home):
    r = _run_cli(isolated_home, "plugins", "install", str(REAL_BOOKMARK_PATH))
    assert r.returncode == 0, r.stderr
    r2 = _run_cli(isolated_home, "plugins", "list", "--json")
    data = json.loads(r2.stdout)
    names = [p["name"] for p in data]
    assert any("bookmark" in n for n in names)


# ---------------------------------------------------------------------------
# skills
# ---------------------------------------------------------------------------


def test_skills_list_empty_active(isolated_home):
    r = _run_cli(isolated_home, "skills", "list", "--active", "--json")
    assert r.returncode == 0
    assert json.loads(r.stdout) == []


def test_skills_list_after_install(isolated_home, tmp_path):
    plugin_src = _write_minimal_plugin(tmp_path)
    _run_cli(isolated_home, "plugins", "install", str(plugin_src), check=True)
    r = _run_cli(isolated_home, "skills", "list", "--json")
    data = json.loads(r.stdout)
    names = [s["name"] for s in data]
    assert "demo-skill" in names


def test_skills_activate_and_deactivate(isolated_home, tmp_path):
    plugin_src = _write_minimal_plugin(tmp_path)
    _run_cli(isolated_home, "plugins", "install", str(plugin_src), check=True)

    r = _run_cli(isolated_home, "skills", "activate", "demo-skill")
    assert r.returncode == 0, r.stderr

    r2 = _run_cli(isolated_home, "skills", "list", "--active", "--json")
    data = json.loads(r2.stdout)
    assert [s["name"] for s in data] == ["demo-skill"]

    r3 = _run_cli(isolated_home, "skills", "deactivate", "demo-skill")
    assert r3.returncode == 0

    r4 = _run_cli(isolated_home, "skills", "list", "--active", "--json")
    assert json.loads(r4.stdout) == []


def test_skills_activate_unknown_exits_nonzero(isolated_home):
    r = _run_cli(isolated_home, "skills", "activate", "nope")
    assert r.returncode != 0
