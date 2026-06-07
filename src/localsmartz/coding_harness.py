"""Read-only coding harness for local workspace questions.

This path is intentionally smaller than the full DeepAgents graph: it gathers
repo context deterministically, then asks the selected local model to answer
against that context. v1 is read-only so Local Smartz does not pretend it has
edited files when it has only inspected them.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Iterable

from langchain_ollama import ChatOllama

from localsmartz.profiles import get_model


_SKIP_DIRS = {
    ".git",
    ".venv",
    ".build-loop",
    ".claude-code-debugger",
    ".ibr",
    ".localsmartz",
    ".navgator",
    "__pycache__",
    "node_modules",
    "DerivedData",
    "dist",
    "build",
    "Library",
    "Applications",
    "Movies",
    "Music",
    "Pictures",
    "Downloads",
    ".mypy_cache",
    ".pytest_cache",
}

_SKIP_FILES = {
    ".DS_Store",
}

_ROOT_CONTEXT_FILES = (
    "AGENTS.md",
    "README.md",
    "HANDOFF.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "justfile",
    ".localsmartz/config.json",
    ".codex-plugin/plugin.json",
)

_RELEVANT_FILES_BY_TERM = {
    "plugin": (
        "src/localsmartz/plugins/agent_integration.py",
        "src/localsmartz/plugins/loader.py",
        "src/localsmartz/plugins/registry.py",
    ),
    "mcp": (
        "src/localsmartz/plugins/agent_integration.py",
        "src/localsmartz/plugins/mcp_client.py",
    ),
    "model": (
        "src/localsmartz/profiles.py",
        "src/localsmartz/ollama.py",
        "src/localsmartz/runners/local_ollama.py",
    ),
    "ollama": (
        "src/localsmartz/profiles.py",
        "src/localsmartz/ollama.py",
        "src/localsmartz/runners/local_ollama.py",
    ),
    "harness": (
        "src/localsmartz/__main__.py",
        "src/localsmartz/agent.py",
        "src/localsmartz/routing.py",
    ),
    "coding": (
        "src/localsmartz/__main__.py",
        "src/localsmartz/agent.py",
        "src/localsmartz/routing.py",
    ),
    "build-loop": (
        "README.md",
        "AGENTS.md",
        "skills/build-loop/SKILL.md",
        ".codex-plugin/plugin.json",
    ),
}

_PATH_RE = re.compile(r"(?:~|/Users/|/private/|/tmp/|\./|\../)[^\s,;:)]+")

_CODING_SYSTEM_PROMPT = """\
You are Local Smartz in coding-harness mode.

Use the supplied workspace context before answering. Do not invent SDKs,
plugins, files, commands, or repo capabilities that are not present in the
context. If the context is insufficient, say what is missing.

Default posture:
- Answer the user's question directly first.
- For coding harness questions, separate what can be assessed now from what
  needs read/write tool support or user credentials.
- If asked about build-loop, plugins, or model selection, reason from the
  provided files and paths.
- This v1 harness is read-only. Do not claim files were modified.
- Keep the answer concise and logically structured.
"""


def resolve_coding_model(profile: dict, model_override: str | None = None) -> str:
    """Pick the model for coding prompts.

    Explicit CLI/UI overrides win. Otherwise use the execution model so
    full-profile installs prefer the coder model instead of the general
    planning model.
    """
    if model_override:
        return model_override
    return (
        get_model(profile, "execution")
        or get_model(profile, "planning")
        or str(profile.get("planning_model", ""))
    )


def _run_cmd(args: list[str], cwd: Path, timeout: float = 2.0) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return f"[unavailable: {exc}]"
    output = (result.stdout or "").strip()
    if result.returncode != 0:
        err = (result.stderr or "").strip()
        return f"[exit {result.returncode}] {err or output}".strip()
    return output


def _git_root(path: Path) -> Path | None:
    out = _run_cmd(["git", "rev-parse", "--show-toplevel"], path)
    if out.startswith("[") or not out:
        return None
    root = Path(out).expanduser()
    return root if root.is_dir() else None


def _iter_files(root: Path, *, limit: int = 140, max_depth: int = 4) -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        try:
            rel = current.relative_to(root)
            depth = 0 if str(rel) == "." else len(rel.parts)
        except ValueError:
            depth = max_depth + 1
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and depth < max_depth
        ]
        for filename in filenames:
            if len(files) >= limit:
                return sorted(files)
            if filename in _SKIP_FILES:
                continue
            path = current / filename
            try:
                files.append(str(path.relative_to(root)))
            except ValueError:
                files.append(str(path))
    return sorted(files)


def _safe_read(path: Path, *, max_lines: int = 140) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"[could not read: {exc}]"
    body = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        body += f"\n[truncated: {len(lines) - max_lines} more lines]"
    return body


def _excerpt(path: Path, terms: Iterable[str], *, max_lines: int = 120) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"[could not read: {exc}]"

    lowered_terms = [term.lower() for term in terms if term]
    hits: list[int] = []
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if any(term in lowered for term in lowered_terms):
            hits.append(idx)
        if len(hits) >= 12:
            break

    if not hits:
        selected = list(range(min(max_lines, len(lines))))
    else:
        selected_set: set[int] = set()
        for hit in hits:
            start = max(0, hit - 4)
            end = min(len(lines), hit + 12)
            selected_set.update(range(start, end))
        selected = sorted(selected_set)[:max_lines]

    rendered: list[str] = []
    last = -2
    for idx in selected:
        if idx != last + 1 and rendered:
            rendered.append("...")
        rendered.append(f"{idx + 1}: {lines[idx]}")
        last = idx
    if len(selected) < len(lines):
        rendered.append(f"[excerpted from {len(lines)} total lines]")
    return "\n".join(rendered)


def _extract_paths(prompt: str, cwd: Path) -> list[Path]:
    out: list[Path] = []
    for raw in _PATH_RE.findall(prompt):
        cleaned = raw.strip().rstrip(".,")
        path = Path(cleaned).expanduser()
        if not path.is_absolute():
            path = (cwd / path).resolve()
        if path.exists() and path not in out:
            out.append(path)
    return out[:4]


def _default_root(prompt: str, cwd: Path) -> Path:
    git_root = _git_root(cwd)
    if git_root:
        return git_root

    lowered = prompt.lower()
    if "localsmartz" in lowered or "local smartz" in lowered:
        local_smartz = Path.home() / "dev/git-folder/local-smartz"
        if local_smartz.is_dir():
            return local_smartz.resolve()

    return cwd


def _prompt_terms(prompt: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", prompt.lower())
    terms = list(dict.fromkeys(words))
    for extra in ("plugin", "mcp", "model", "ollama", "harness", "coding", "fast_path"):
        if extra not in terms:
            terms.append(extra)
    return terms[:24]


def _add_root_context(parts: list[str], root: Path, label: str, prompt_terms: list[str]) -> None:
    parts.append(f"## {label}: {root}")
    git_root = _git_root(root)
    if git_root:
        parts.append(f"Git root: {git_root}")
        status = _run_cmd(["git", "status", "--short", "--branch"], git_root)
        parts.append(f"Git status:\n{status or '(clean)'}")

    files = _iter_files(root)
    if files:
        parts.append("File inventory (truncated):\n" + "\n".join(files[:140]))

    seen: set[Path] = set()
    for rel in _ROOT_CONTEXT_FILES:
        path = root / rel
        if path.is_file():
            seen.add(path)
            parts.append(f"### {rel}\n{_safe_read(path)}")

    term_text = " ".join(prompt_terms)
    for term, rels in _RELEVANT_FILES_BY_TERM.items():
        if term not in term_text:
            continue
        for rel in rels:
            path = root / rel
            if path.is_file() and path not in seen:
                seen.add(path)
                parts.append(f"### {rel} relevant excerpt\n{_excerpt(path, prompt_terms)}")


def build_coding_context(prompt: str, cwd: Path) -> str:
    """Collect deterministic local context for a coding prompt."""
    cwd = cwd.expanduser().resolve()
    root = _default_root(prompt, cwd)
    terms = _prompt_terms(prompt)
    parts: list[str] = []
    _add_root_context(parts, root, "Primary workspace", terms)

    for path in _extract_paths(prompt, cwd):
        target = path if path.is_dir() else path.parent
        if target.resolve() == root.resolve():
            continue
        _add_root_context(parts, target.resolve(), "Referenced path", terms)
        if path.is_file():
            parts.append(f"### Referenced file: {path}\n{_excerpt(path, terms)}")

    return "\n\n".join(parts)


def coding_harness_stream(
    prompt: str,
    profile: dict,
    *,
    cwd: Path,
    model_override: str | None = None,
):
    """Yield SSE-style events for a read-only coding-harness response."""
    import httpx

    start = time.time()
    model_name = resolve_coding_model(profile, model_override)
    yield {
        "type": "text",
        "content": f"[coding-harness] using {model_name} with read-only repo context\n\n",
    }
    yield {"type": "stage", "stage": "workspace_context"}

    context = build_coding_context(prompt, cwd)
    user = (
        f"## User Prompt\n{prompt}\n\n"
        f"## Workspace Context\n{context}\n\n"
        "Answer from the context above."
    )

    llm = ChatOllama(
        model=model_name,
        temperature=0,
        num_ctx=8192,
        keep_alive="30m",
        client_kwargs={
            "timeout": httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0),
        },
    ).with_retry(
        stop_after_attempt=2,
        wait_exponential_jitter=True,
        retry_if_exception_type=(httpx.TransportError, httpx.TimeoutException),
    )

    try:
        for chunk in llm.stream(
            [
                {"role": "system", "content": _CODING_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ]
        ):
            content = getattr(chunk, "content", None)
            if isinstance(content, str) and content:
                yield {"type": "text", "content": content}
            elif isinstance(content, list):
                for seg in content:
                    text = seg.get("text") if isinstance(seg, dict) else None
                    if isinstance(text, str) and text:
                        yield {"type": "text", "content": text}
    except Exception as exc:  # noqa: BLE001
        yield {
            "type": "tool_error",
            "name": "coding_harness",
            "message": f"Coding harness LLM call failed: {exc}",
        }

    yield {
        "type": "done",
        "duration_ms": int((time.time() - start) * 1000),
        "thread_id": "",
    }


__all__ = [
    "build_coding_context",
    "coding_harness_stream",
    "resolve_coding_model",
]
