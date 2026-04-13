"""Validation for plugin.json, SKILL.md, commands, hooks.json, .mcp.json.

Emits ValidationReport with coded, actionable issues.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from localsmartz.plugins.discovery import (
    find_command_files,
    find_hooks_file,
    find_mcp_file,
    find_skill_dirs,
    is_plugin_dir,
    is_skill_dir,
)

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    path: str


@dataclass
class ValidationReport:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        if issue.severity == "error":
            self.ok = False

    def extend(self, other: "ValidationReport") -> None:
        for issue in other.issues:
            self.add(issue)


def _err(code: str, message: str, path: Path | str) -> ValidationIssue:
    return ValidationIssue("error", code, message, str(path))


def _warn(code: str, message: str, path: Path | str) -> ValidationIssue:
    return ValidationIssue("warning", code, message, str(path))


# ---------------------------------------------------------------------------
# Frontmatter parser (handwritten — avoids pyyaml dep)
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, str] | None, str, str | None]:
    """Parse ``---\\nkey: value\\n---\\nbody`` frontmatter.

    Returns ``(data, body, error)``. ``data`` is None if there's no frontmatter.
    ``error`` is a message if frontmatter was malformed.
    Values are strings; ``true``/``false`` are left as strings — callers coerce.
    """
    if not text.startswith("---"):
        return None, text, None
    # first line must be exactly ---
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None, text, None
    # find closing ---
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return None, text, "No closing '---' delimiter for frontmatter"

    data: dict[str, str] = {}
    current_key: str | None = None
    for raw in lines[1:end_idx]:
        line = raw.rstrip()
        if not line.strip():
            continue
        # continuation line (folded/multiline value): starts with whitespace
        if line[:1] in (" ", "\t") and current_key is not None:
            data[current_key] = (data[current_key] + " " + line.strip()).strip()
            continue
        if ":" not in line:
            return None, text, f"Malformed frontmatter line: {line!r}"
        key, _, value = line.partition(":")
        key = key.strip()
        if not key or not _NAME_RE.match(key.replace("_", "-")):
            # permissive on keys; just require something usable
            if not key:
                return None, text, f"Empty key in frontmatter line: {line!r}"
        value = value.strip()
        # strip matching surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        data[key] = value
        current_key = key

    body = "\n".join(lines[end_idx + 1 :])
    return data, body, None


def _to_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    v = raw.strip().lower()
    if v in ("true", "yes", "1"):
        return True
    if v in ("false", "no", "0"):
        return False
    return None


# ---------------------------------------------------------------------------
# plugin.json
# ---------------------------------------------------------------------------

def validate_plugin_json(path: Path) -> ValidationReport:
    report = ValidationReport(ok=True)
    if not path.is_file():
        report.add(_err("MISSING_PLUGIN_JSON", f"plugin.json not found at {path}", path))
        return report
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        report.add(_err("UNREADABLE_FILE", f"Cannot read plugin.json: {e}", path))
        return report
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        report.add(_err("INVALID_JSON", f"plugin.json is not valid JSON: {e}", path))
        return report
    if not isinstance(data, dict):
        report.add(_err("INVALID_JSON", "plugin.json must be a JSON object", path))
        return report

    name = data.get("name")
    if not name:
        report.add(_err("MISSING_NAME", "plugin.json missing 'name'", path))
    elif not isinstance(name, str) or not _NAME_RE.match(name):
        report.add(_err("INVALID_NAME", f"plugin name must be kebab-case, got {name!r}", path))

    version = data.get("version")
    if not version:
        report.add(_err("MISSING_VERSION", "plugin.json missing 'version'", path))
    elif not isinstance(version, str) or not _SEMVER_RE.match(version):
        report.add(_err("INVALID_SEMVER", f"version must be semver, got {version!r}", path))

    description = data.get("description")
    if not description:
        report.add(_err("MISSING_DESCRIPTION", "plugin.json missing 'description'", path))
    elif not isinstance(description, str) or len(description) < 10:
        report.add(_err("INVALID_DESCRIPTION", "description must be >10 chars", path))

    author = data.get("author")
    if author is None:
        report.add(_err("MISSING_AUTHOR", "plugin.json missing 'author'", path))
    elif not isinstance(author, dict) or not str(author.get("name", "")).strip():
        report.add(_err("INVALID_AUTHOR", "author.name must be non-empty string", path))

    # optional fields — shape check only
    keywords = data.get("keywords")
    if keywords is not None and not (
        isinstance(keywords, list) and all(isinstance(k, str) for k in keywords)
    ):
        report.add(_warn("INVALID_KEYWORDS", "keywords should be list of strings", path))

    return report


# ---------------------------------------------------------------------------
# SKILL.md
# ---------------------------------------------------------------------------

def validate_skill_md(path: Path) -> ValidationReport:
    report = ValidationReport(ok=True)
    if not path.is_file():
        report.add(_err("MISSING_SKILL_MD", f"SKILL.md not found at {path}", path))
        return report
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        report.add(_err("UNREADABLE_FILE", f"Cannot read SKILL.md: {e}", path))
        return report

    data, body, err = parse_frontmatter(text)
    if data is None:
        if err:
            report.add(_err("MALFORMED_YAML", f"SKILL.md frontmatter: {err}", path))
        else:
            report.add(_err("MISSING_FRONTMATTER", "SKILL.md missing YAML frontmatter", path))
        return report

    name = data.get("name")
    if not name:
        report.add(_err("MISSING_NAME", "SKILL.md frontmatter missing 'name'", path))
    elif not _NAME_RE.match(name):
        report.add(
            _err("INVALID_NAME", f"skill name must match ^[a-z0-9-]+$, got {name!r}", path)
        )

    description = data.get("description", "").strip()
    if not description:
        report.add(_err("MISSING_DESCRIPTION", "SKILL.md missing 'description'", path))
    else:
        # lenient length check — just warn
        if len(description) < 80 or len(description) > 200:
            report.add(
                _warn(
                    "DESCRIPTION_LENGTH",
                    f"description length {len(description)} outside recommended 80-200 range",
                    path,
                )
            )

    version = data.get("version")
    if version and not _SEMVER_RE.match(version):
        report.add(_err("INVALID_SEMVER", f"version must be semver, got {version!r}", path))

    canonical = _to_bool(data.get("canonical"))
    if canonical:
        if not data.get("source-plugin"):
            report.add(
                _err(
                    "MISSING_SOURCE_PLUGIN",
                    "canonical: true requires 'source-plugin'",
                    path,
                )
            )
        if not data.get("source-repo"):
            report.add(
                _err(
                    "MISSING_SOURCE_REPO",
                    "canonical: true requires 'source-repo'",
                    path,
                )
            )

    if len(body.encode("utf-8")) <= 100:
        report.add(
            _err(
                "SKILL_BODY_TOO_SHORT",
                f"SKILL.md body must be >100 bytes (got {len(body.encode('utf-8'))})",
                path,
            )
        )

    return report


# ---------------------------------------------------------------------------
# Command files
# ---------------------------------------------------------------------------

def validate_command_md(path: Path) -> ValidationReport:
    report = ValidationReport(ok=True)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        report.add(_err("UNREADABLE_FILE", f"Cannot read command: {e}", path))
        return report

    data, _body, err = parse_frontmatter(text)
    if data is None:
        if err:
            report.add(_err("MALFORMED_YAML", f"command frontmatter: {err}", path))
        else:
            report.add(_err("MISSING_FRONTMATTER", "command missing frontmatter", path))
        return report

    if not data.get("description", "").strip():
        report.add(_err("MISSING_DESCRIPTION", "command missing 'description'", path))

    tools_raw = data.get("allowed-tools")
    if not tools_raw:
        report.add(_err("MISSING_ALLOWED_TOOLS", "command missing 'allowed-tools'", path))
    else:
        tools = [t.strip() for t in tools_raw.split(",") if t.strip()]
        if not tools:
            report.add(_err("MISSING_ALLOWED_TOOLS", "allowed-tools is empty", path))

    return report


# ---------------------------------------------------------------------------
# hooks.json
# ---------------------------------------------------------------------------

_VALID_HOOK_EVENTS = {
    "Stop",
    "PreCompact",
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "SubagentStop",
    "Notification",
}


def validate_hooks_json(path: Path) -> ValidationReport:
    report = ValidationReport(ok=True)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        report.add(_err("UNREADABLE_FILE", f"Cannot read hooks.json: {e}", path))
        return report
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        report.add(_err("INVALID_JSON", f"hooks.json is not valid JSON: {e}", path))
        return report
    if not isinstance(data, dict) or "hooks" not in data:
        report.add(_err("INVALID_HOOKS", "hooks.json must have top-level 'hooks' object", path))
        return report
    hooks = data["hooks"]
    if not isinstance(hooks, dict):
        report.add(_err("INVALID_HOOKS", "'hooks' must be an object", path))
        return report

    for event, entries in hooks.items():
        if event not in _VALID_HOOK_EVENTS:
            report.add(_warn("UNKNOWN_HOOK_EVENT", f"Unknown hook event '{event}'", path))
        if not isinstance(entries, list):
            report.add(_err("INVALID_HOOKS", f"hooks['{event}'] must be a list", path))
            continue
        for entry in entries:
            # Two supported shapes: direct {type, command, ...} or {matcher, hooks: [...]}
            items: list[dict] = []
            if isinstance(entry, dict) and "hooks" in entry and isinstance(entry["hooks"], list):
                items = [h for h in entry["hooks"] if isinstance(h, dict)]
            elif isinstance(entry, dict):
                items = [entry]
            else:
                report.add(
                    _err("INVALID_HOOKS", f"hooks['{event}'] entries must be objects", path)
                )
                continue
            for h in items:
                htype = h.get("type")
                if htype != "command":
                    report.add(
                        _err(
                            "INVALID_HOOK_TYPE",
                            f"hook type must be 'command', got {htype!r}",
                            path,
                        )
                    )
                cmd = h.get("command")
                if not isinstance(cmd, str) or not cmd.strip():
                    report.add(
                        _err("INVALID_HOOK_COMMAND", "hook 'command' must be non-empty string", path)
                    )
                timeout = h.get("timeout")
                if timeout is not None:
                    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
                        report.add(
                            _err(
                                "INVALID_TIMEOUT",
                                f"hook 'timeout' must be positive int, got {timeout!r}",
                                path,
                            )
                        )
    return report


# ---------------------------------------------------------------------------
# .mcp.json
# ---------------------------------------------------------------------------

def validate_mcp_json(path: Path) -> ValidationReport:
    report = ValidationReport(ok=True)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        report.add(_err("UNREADABLE_FILE", f"Cannot read .mcp.json: {e}", path))
        return report
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        report.add(_err("INVALID_JSON", f".mcp.json is not valid JSON: {e}", path))
        return report
    if not isinstance(data, dict):
        report.add(_err("INVALID_MCP", ".mcp.json must be a JSON object", path))
        return report
    # Support both {"mcpServers": {...}} and direct {name: {...}}
    servers = data.get("mcpServers") if "mcpServers" in data else data
    if not isinstance(servers, dict):
        report.add(_err("INVALID_MCP", "mcp servers must be an object", path))
        return report
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            report.add(_err("INVALID_MCP_SERVER", f"server '{name}' spec must be object", path))
            continue
        cmd = spec.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            report.add(
                _err("INVALID_MCP_COMMAND", f"server '{name}' missing 'command'", path)
            )
        args = spec.get("args", [])
        if args is not None and not (
            isinstance(args, list) and all(isinstance(a, str) for a in args)
        ):
            report.add(
                _err("INVALID_MCP_ARGS", f"server '{name}' 'args' must be list[str]", path)
            )
    return report


# ---------------------------------------------------------------------------
# Whole-plugin validation
# ---------------------------------------------------------------------------

def validate_plugin(plugin_root: Path) -> ValidationReport:
    report = ValidationReport(ok=True)
    if not plugin_root.is_dir():
        report.add(_err("MISSING_PLUGIN_DIR", f"Plugin dir not found: {plugin_root}", plugin_root))
        return report

    if is_plugin_dir(plugin_root):
        report.extend(validate_plugin_json(plugin_root / ".claude-plugin" / "plugin.json"))
    elif is_skill_dir(plugin_root):
        # treat as standalone skill
        report.extend(validate_skill_md(plugin_root / "SKILL.md"))
        return report
    else:
        report.add(
            _err(
                "NOT_A_PLUGIN",
                "Directory is neither a plugin (.claude-plugin/plugin.json) nor a standalone skill (SKILL.md)",
                plugin_root,
            )
        )
        return report

    for skill_dir in find_skill_dirs(plugin_root):
        report.extend(validate_skill_md(skill_dir / "SKILL.md"))

    for cmd_path in find_command_files(plugin_root):
        report.extend(validate_command_md(cmd_path))

    hooks = find_hooks_file(plugin_root)
    if hooks is not None:
        report.extend(validate_hooks_json(hooks))

    mcp = find_mcp_file(plugin_root)
    if mcp is not None:
        report.extend(validate_mcp_json(mcp))

    return report
