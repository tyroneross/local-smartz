from __future__ import annotations

from pathlib import Path

from localsmartz.coding_harness import build_coding_context, resolve_coding_model


def test_resolve_coding_model_prefers_execution_model():
    profile = {
        "planning_model": "gpt-oss:20b",
        "execution_model": "qwen2.5-coder:32b-instruct-q5_K_M",
    }

    assert resolve_coding_model(profile) == "qwen2.5-coder:32b-instruct-q5_K_M"
    assert resolve_coding_model(profile, "custom:7b") == "custom:7b"


def test_build_coding_context_resolves_localsmartz_repo_from_home(
    tmp_path: Path,
    monkeypatch,
):
    home = tmp_path / "home"
    repo = home / "dev/git-folder/local-smartz"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# Local Smartz\n\nRepo readme.", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[project]\nname = \"localsmartz\"\n",
        encoding="utf-8",
    )
    cwd = home
    monkeypatch.setenv("HOME", str(home))

    context = build_coding_context(
        "how can we use localsmartz as a coding harness?",
        cwd,
    )

    assert f"Primary workspace: {repo.resolve()}" in context
    assert "# Local Smartz" in context
    assert "name = \"localsmartz\"" in context
