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


def test_build_coding_context_includes_nextjs_persona_files(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "components").mkdir()
    (tmp_path / "src/lib").mkdir(parents=True)
    (tmp_path / "app/page.tsx").write_text(
        "export default function Home() {}",
        encoding="utf-8",
    )
    (tmp_path / "app/layout.tsx").write_text(
        "export default function Layout() {}",
        encoding="utf-8",
    )
    (tmp_path / "app/competitive-research").mkdir()
    (tmp_path / "app/competitive-research/page.tsx").write_text(
        "export default function CompetitiveResearch() {}",
        encoding="utf-8",
    )
    (tmp_path / ".next/server/app/competitive-research").mkdir(parents=True)
    (tmp_path / ".next/server/app/competitive-research/page.js").write_text(
        "compiled route artifact",
        encoding="utf-8",
    )
    (tmp_path / "components/AppShell.tsx").write_text(
        "export function AppShell() {}",
        encoding="utf-8",
    )
    (tmp_path / "src/lib/fixtures.ts").write_text(
        "export const personas = []",
        encoding="utf-8",
    )
    (tmp_path / "src/lib/persona.ts").write_text(
        "export type Persona = {}",
        encoding="utf-8",
    )
    (tmp_path / "src/lib/repository.ts").write_text(
        "export function getPersonas() {}",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"next":"latest"}}',
        encoding="utf-8",
    )

    context = build_coding_context(
        "In this Next.js persona app, propose a competitive research screen.",
        tmp_path,
    )

    assert "### app/page.tsx relevant excerpt" in context
    assert "### app/competitive-research/page.tsx relevant excerpt" in context
    assert "### components/AppShell.tsx relevant excerpt" in context
    assert "### src/lib/fixtures.ts relevant excerpt" in context
    assert "### src/lib/persona.ts relevant excerpt" in context
    assert "### src/lib/repository.ts relevant excerpt" in context
    assert ".next/server/app/competitive-research/page.js" not in context
    assert context.index("### app/competitive-research/page.tsx") < context.index(
        "File inventory"
    )
    assert context.index("### app/competitive-research/page.tsx") < context.index(
        "### package.json"
    )
