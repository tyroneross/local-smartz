"""Tests for `localsmartz doctor` diagnostic matrix."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from unittest.mock import patch

from localsmartz import doctor


class _FakeResp:
    """Minimal stand-in for urllib's response object."""

    def __init__(self, code: int, body: bytes = b"", lines: list[bytes] | None = None):
        self._code = code
        self._body = body
        self._lines = lines or []

    def getcode(self) -> int:
        return self._code

    def read(self) -> bytes:
        return self._body

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _url_opener(route_map: dict[str, _FakeResp | Exception]):
    """Build an urlopen stand-in that looks up responses by URL (prefix match)."""

    def _opener(req, timeout=None):  # noqa: ARG001
        url = req if isinstance(req, str) else req.full_url
        for key, val in route_map.items():
            if url.startswith(key):
                if isinstance(val, Exception):
                    raise val
                return val
        raise ConnectionRefusedError(f"no route for {url}")

    return _opener


def test_ollama_unreachable_fails_and_skips_downstream():
    # Every urlopen call raises — ollama is down, no backend.
    with patch(
        "urllib.request.urlopen",
        side_effect=ConnectionRefusedError("nope"),
    ):
        results = doctor.run_doctor()

    status = {name: (s, h) for (name, s, h) in results}
    assert status["ollama_reachable"][0] == "fail"
    assert "Start Ollama" in status["ollama_reachable"][1]
    assert status["models_present"][0] == "skip"
    assert status["backend_up"][0] == "skip"
    assert status["sse_healthy"][0] == "skip"
    # Pure-Python classifier never depends on the network.
    assert status["fast_path_classifier"][0] == "ok"


def test_models_missing_reports_fail_with_install_hint():
    tags_body = json.dumps({"models": [{"name": "some-other-model:latest"}]}).encode()
    opener = _url_opener(
        {
            "http://localhost:11434/api/tags": _FakeResp(200, tags_body),
            # Backend down
            "http://localhost:11435": ConnectionRefusedError("down"),
            "http://localhost:11436": ConnectionRefusedError("down"),
        }
    )
    with patch("urllib.request.urlopen", side_effect=opener):
        results = doctor.run_doctor()

    status = {name: (s, h) for (name, s, h) in results}
    assert status["ollama_reachable"][0] == "ok"
    assert status["models_present"][0] == "fail"
    assert "ollama pull" in status["models_present"][1]
    assert status["backend_up"][0] == "skip"


def test_models_present_exact_tag_absent_never_reported_ready():
    """DEFECT FIX (item 7c): a tag absent from live /api/tags must never
    be reported ready, even when the base family/a fuzzy-matching variant
    IS installed. Regression guard for the old prefix-scan bug that
    reported the uninstalled 'qwen2.5-coder:32b-instruct-q5_K_M' as ready
    because *some* tag started with 'qwen2.5-coder:32b'."""
    # Only the base tag is "installed" — the exact requested tag isn't.
    tags_body = json.dumps({"models": [{"name": "qwen2.5-coder:32b"}]}).encode()
    opener = _url_opener({
        "http://localhost:11434/api/tags": _FakeResp(200, tags_body),
        "http://localhost:11435": ConnectionRefusedError("down"),
        "http://localhost:11436": ConnectionRefusedError("down"),
    })
    mock_profile = {
        "name": "full",
        "planning_model": "gpt-oss:20b",
        "execution_model": "qwen2.5-coder:32b-instruct-q5_K_M",  # NOT installed
    }
    with patch("urllib.request.urlopen", side_effect=opener), \
         patch("localsmartz.profiles.get_profile", return_value=mock_profile):
        results = doctor.run_doctor()

    status = {name: (s, h) for (name, s, h) in results}
    assert status["models_present"][0] == "fail"
    assert "qwen2.5-coder:32b-instruct-q5_K_M" in status["models_present"][1]


def test_models_present_exact_tags_installed_reports_ready():
    tags_body = json.dumps({
        "models": [{"name": "gpt-oss:20b"}, {"name": "qwen2.5-coder:32b"}],
    }).encode()
    opener = _url_opener({
        "http://localhost:11434/api/tags": _FakeResp(200, tags_body),
        "http://localhost:11435": ConnectionRefusedError("down"),
        "http://localhost:11436": ConnectionRefusedError("down"),
    })
    mock_profile = {
        "name": "full",
        "planning_model": "gpt-oss:20b",
        "execution_model": "qwen2.5-coder:32b",
    }
    with patch("urllib.request.urlopen", side_effect=opener), \
         patch("localsmartz.profiles.get_profile", return_value=mock_profile):
        results = doctor.run_doctor()

    status = {name: (s, h) for (name, s, h) in results}
    assert status["models_present"][0] == "ok"


def test_models_present_pinned_active_model_uninstalled_reports_not_ready():
    """F4: profiles.global_pinned_model() overrides planning/execution for
    every role at runtime, so --check must validate the PIN's tag too —
    not just the profile defaults it overrides. Pre-fix, --check ignored
    the pin entirely and could report "ready" while the model runtime
    will actually use is not pulled."""
    tags_body = json.dumps({
        "models": [{"name": "gpt-oss:20b"}, {"name": "qwen2.5-coder:32b"}],
    }).encode()
    opener = _url_opener({
        "http://localhost:11434/api/tags": _FakeResp(200, tags_body),
        "http://localhost:11435": ConnectionRefusedError("down"),
        "http://localhost:11436": ConnectionRefusedError("down"),
    })
    mock_profile = {
        "name": "full",
        "planning_model": "gpt-oss:20b",
        "execution_model": "qwen2.5-coder:32b",
    }
    with patch("urllib.request.urlopen", side_effect=opener), \
         patch("localsmartz.profiles.get_profile", return_value=mock_profile), \
         patch("localsmartz.profiles.global_pinned_model", return_value="pinned:not-installed"):
        results = doctor.run_doctor()

    status = {name: (s, h) for (name, s, h) in results}
    assert status["models_present"][0] == "fail"
    assert "pinned:not-installed" in status["models_present"][1]


def test_models_present_pinned_active_model_installed_reports_ready():
    """Pin set AND its tag pulled — still ready (no false-negative)."""
    tags_body = json.dumps({
        "models": [
            {"name": "gpt-oss:20b"},
            {"name": "qwen2.5-coder:32b"},
            {"name": "pinned:installed"},
        ],
    }).encode()
    opener = _url_opener({
        "http://localhost:11434/api/tags": _FakeResp(200, tags_body),
        "http://localhost:11435": ConnectionRefusedError("down"),
        "http://localhost:11436": ConnectionRefusedError("down"),
    })
    mock_profile = {
        "name": "full",
        "planning_model": "gpt-oss:20b",
        "execution_model": "qwen2.5-coder:32b",
    }
    with patch("urllib.request.urlopen", side_effect=opener), \
         patch("localsmartz.profiles.get_profile", return_value=mock_profile), \
         patch("localsmartz.profiles.global_pinned_model", return_value="pinned:installed"):
        results = doctor.run_doctor()

    status = {name: (s, h) for (name, s, h) in results}
    assert status["models_present"][0] == "ok"


def test_fast_path_classifier_check_matches_profiles():
    # This check is pure code: positive case is fast-path, negative is not.
    name, status, _hint = doctor._check_fast_path_classifier()
    assert name == "fast_path_classifier"
    assert status == "ok"


def test_doctor_json_output_shape_via_cli():
    # End-to-end: run the CLI with --json and verify the output shape,
    # regardless of whether ollama/backend happen to be live on this box.
    proc = subprocess.run(
        [sys.executable, "-m", "localsmartz", "doctor", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # Exit code: 0 (all ok/skip) or 1 (one+ fail) — both valid shapes.
    assert proc.returncode in (0, 1), proc.stderr
    data = json.loads(proc.stdout)
    assert isinstance(data, list)
    names = {row["name"] for row in data}
    assert names == {
        "ollama_reachable",
        "models_present",
        "backend_up",
        "sse_healthy",
        "fast_path_classifier",
    }
    for row in data:
        assert row["status"] in ("ok", "fail", "skip")
        assert isinstance(row["hint"], str)
