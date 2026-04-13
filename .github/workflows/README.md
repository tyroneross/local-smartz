# CI Workflows

This directory contains GitHub Actions workflows for local-smartz.

## `python.yml` — Python Tests

Runs the pytest suite on every push to `main`, every pull request, and on manual dispatch. Executes on both `macos-latest` and `ubuntu-latest` with Python 3.12 to catch OS-specific regressions. Installs the package via `pip install -e ".[dev]"` with a fallback to `pip install -e . pytest` when the `dev` extra is not yet declared. Integration tests that require a running Ollama server (`tests/test_ollama.py` and `tests/test_serve.py`) are skipped in CI because the runners do not have Ollama available. JUnit XML results are uploaded as an artifact per-OS for inspection.

## `macos.yml` — macOS App Build

Verifies that the SwiftUI macOS app at `app/LocalSmartz/` still builds. Triggers only when files under `app/**`, `pyproject.toml`, or this workflow itself change (plus manual dispatch), so unrelated Python-only changes don't spend macOS runner minutes. Installs `xcodegen` via Homebrew, regenerates the `.xcodeproj` from `project.yml`, then runs `xcodebuild` in Release configuration with code signing fully disabled — this is a build-verification workflow only, not a distributable build. The resulting unsigned `LocalSmartz.app` is uploaded as an artifact for download.

## Conventions

- All `actions/*` versions are pinned to a major (`@v4`, `@v5`). Never `@latest` or `@main`.
- Multi-line bash steps use `set -euo pipefail`.
- No secrets are required by either workflow.
