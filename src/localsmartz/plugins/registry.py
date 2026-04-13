"""Plugin registry: install/remove/list + active-skills tracking."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from localsmartz.plugins.discovery import find_plugin_dirs, is_plugin_dir, is_skill_dir
from localsmartz.plugins.loader import (
    Command,
    MCPServer,
    Plugin,
    Skill,
    load_plugin,
    load_standalone_skill,
)
from localsmartz.plugins.validator import (
    ValidationIssue,
    ValidationReport,
    validate_plugin,
    validate_plugin_json,
    validate_skill_md,
)

def _default_root() -> Path:
    return Path.home() / ".localsmartz" / "plugins"


def _active_skills_file() -> Path:
    return Path.home() / ".localsmartz" / "active_skills.json"


class ValidationError(Exception):
    """Raised when install() receives a plugin that fails validation."""

    def __init__(self, report: ValidationReport):
        self.report = report
        messages = [
            f"[{i.severity.upper()}] {i.code}: {i.message} ({i.path})"
            for i in report.issues
            if i.severity == "error"
        ]
        super().__init__("Validation failed:\n" + "\n".join(messages))


class Registry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_default_root(cls) -> "Registry":
        return cls(_default_root())

    # ------------------------------------------------------------------
    # Install / remove
    # ------------------------------------------------------------------

    def install(
        self,
        source_path: Path,
        dest_name: str | None = None,
        copy: bool = False,
    ) -> Plugin:
        """Install plugin from `source_path` into `self.root/<dest_name>`.

        Default is symlink; set ``copy=True`` for a real copy.
        ``dest_name`` defaults to plugin.json's name.
        """
        source = Path(source_path).expanduser().resolve()
        report = self.validate(source)
        if not report.ok:
            raise ValidationError(report)

        if not is_plugin_dir(source):
            raise ValidationError(
                ValidationReport(
                    ok=False,
                    issues=[
                        ValidationIssue(
                            "error",
                            "NOT_A_PLUGIN",
                            "Source is not a plugin directory",
                            str(source),
                        )
                    ],
                )
            )

        # derive name if not provided
        if dest_name is None:
            meta = json.loads((source / ".claude-plugin" / "plugin.json").read_text("utf-8"))
            dest_name = meta["name"]

        dest = self.root / dest_name
        if dest.exists() or dest.is_symlink():
            # idempotent-ish: replace existing install
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            else:
                shutil.rmtree(dest)

        if copy:
            shutil.copytree(source, dest, symlinks=False)
        else:
            os.symlink(source, dest, target_is_directory=True)

        plugin = load_plugin(dest)
        if plugin is None:
            raise ValidationError(
                ValidationReport(
                    ok=False,
                    issues=[
                        ValidationIssue(
                            "error",
                            "LOAD_FAILED",
                            "Failed to load plugin after install",
                            str(dest),
                        )
                    ],
                )
            )
        return plugin

    def remove(self, name: str) -> None:
        path = self.root / name
        if not path.exists() and not path.is_symlink():
            return
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        # prune from active skills (skills from removed plugin no longer exist)
        active = set(self.get_active_skill_names())
        remaining_skills = {s.name for s in self.list_skills()}
        pruned = sorted(active & remaining_skills)
        if pruned != sorted(active):
            self.set_active_skill_names(pruned)

    # ------------------------------------------------------------------
    # Listings
    # ------------------------------------------------------------------

    def list_plugins(self) -> list[Plugin]:
        plugins: list[Plugin] = []
        for plugin_dir in find_plugin_dirs(self.root):
            plugin = load_plugin(plugin_dir)
            if plugin is not None:
                plugins.append(plugin)
        return plugins

    def list_skills(self, active_only: bool = False) -> list[Skill]:
        all_skills: list[Skill] = []
        for plugin in self.list_plugins():
            all_skills.extend(plugin.skills)
        # also pick up standalone skills that sit directly under root
        for child in self.root.iterdir() if self.root.is_dir() else []:
            if child.is_dir() and not is_plugin_dir(child) and is_skill_dir(child):
                s = load_standalone_skill(child)
                if s is not None:
                    all_skills.append(s)
        if active_only:
            active = set(self.get_active_skill_names())
            return [s for s in all_skills if s.name in active]
        return all_skills

    def list_commands(self) -> list[Command]:
        out: list[Command] = []
        for plugin in self.list_plugins():
            out.extend(plugin.commands)
        return out

    def list_mcp_servers(self) -> list[MCPServer]:
        out: list[MCPServer] = []
        for plugin in self.list_plugins():
            out.extend(plugin.mcp_servers)
        return out

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self, path: Path) -> ValidationReport:
        """Validate a plugin or standalone skill at arbitrary path."""
        path = Path(path).expanduser()
        if is_plugin_dir(path):
            return validate_plugin(path)
        if is_skill_dir(path):
            return validate_skill_md(path / "SKILL.md")
        # fallback: maybe they pointed at plugin.json directly
        if path.is_file() and path.name == "plugin.json":
            return validate_plugin_json(path)
        if path.is_file() and path.name == "SKILL.md":
            return validate_skill_md(path)
        # not recognizable — produce structured error
        return ValidationReport(
            ok=False,
            issues=[
                ValidationIssue(
                    "error",
                    "NOT_A_PLUGIN",
                    "Path is not a plugin directory or skill",
                    str(path),
                )
            ],
        )

    # ------------------------------------------------------------------
    # Active skills
    # ------------------------------------------------------------------

    def _active_file(self) -> Path:
        return _active_skills_file()

    def get_active_skill_names(self) -> list[str]:
        path = self._active_file()
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, dict):
            return []
        names = data.get("active", [])
        if not isinstance(names, list):
            return []
        return [str(n) for n in names if isinstance(n, str)]

    def set_active_skill_names(self, names: list[str]) -> None:
        path = self._active_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        # dedupe + sort for stable output
        cleaned = sorted({str(n) for n in names if isinstance(n, str) and n.strip()})
        path.write_text(json.dumps({"active": cleaned}, indent=2), encoding="utf-8")
