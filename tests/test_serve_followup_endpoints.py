"""Tests for the follow-on endpoints added in 2026-04-23 build 2:

- PUT /api/agents/<role>/prompt  — writes prompts/<role>.md
- POST /api/evals/run              — runs the golden-task suite
- Thread pattern pinning           — 409 on cross-pattern re-use
"""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path

import pytest

from localsmartz.serve import LocalSmartzHandler


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), LocalSmartzHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()


def _put_json(port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(payload)
    conn.request(
        "PUT",
        path,
        body=body,
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    status = resp.status
    response_body = resp.read().decode("utf-8")
    conn.close()
    try:
        return status, json.loads(response_body)
    except json.JSONDecodeError:
        return status, {"_raw": response_body}


def _post_json(port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    body = json.dumps(payload)
    conn.request(
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    status = resp.status
    response_body = resp.read().decode("utf-8")
    conn.close()
    try:
        return status, json.loads(response_body)
    except json.JSONDecodeError:
        return status, {"_raw": response_body}


# ── PUT prompt endpoint (Item 1) ─────────────────────────────────────────

def test_put_agent_prompt_writes_md(server: int, tmp_path: Path, monkeypatch) -> None:
    # Redirect the prompts dir into a tmp so we don't clobber shipped files.
    from localsmartz.agents import definitions as defs
    monkeypatch.setattr(defs, "_PROMPTS_DIR", tmp_path)

    status, data = _put_json(
        server,
        "/api/agents/researcher/prompt",
        {"system_focus": "Custom researcher prompt.\nLine two."},
    )
    assert status == 200, data
    assert data["ok"] is True
    assert data["role"] == "researcher"
    written = (tmp_path / "researcher.md").read_text()
    assert written.startswith("Custom researcher prompt.")


def test_put_agent_prompt_rejects_path_traversal(server: int) -> None:
    status, _ = _put_json(
        server,
        "/api/agents/..%2Fetc%2Fpasswd/prompt",
        {"system_focus": "nope"},
    )
    # 404 (no route) or 400 (invalid role) both acceptable — just not 200.
    assert status in (400, 404)


def test_put_agent_prompt_requires_non_empty(server: int, tmp_path: Path, monkeypatch) -> None:
    from localsmartz.agents import definitions as defs
    monkeypatch.setattr(defs, "_PROMPTS_DIR", tmp_path)

    status, _ = _put_json(
        server,
        "/api/agents/researcher/prompt",
        {"system_focus": ""},
    )
    assert status == 400


# ── POST agent model endpoint ──────────────────────────────────────────────

def test_post_agent_model_empty_clears_override(
    server: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    status, data = _post_json(
        server,
        "/api/agents/researcher/model",
        {"model": "qwen3:8b-q4_K_M"},
    )
    assert status == 200, data

    status, data = _post_json(
        server,
        "/api/agents/researcher/model",
        {"model": ""},
    )
    assert status == 200, data
    assert data["model"] == ""

    from localsmartz import global_config

    assert global_config.get("agent_models") == {}


# ── Golden-task eval runner endpoint (Item 6) ────────────────────────────

def test_post_evals_run_uses_runner(server: int, monkeypatch) -> None:
    # Stub the harness so we don't hit Ollama/Anthropic in the test.
    from localsmartz import benchmarking as bm

    def _fake(provider, *, model=None, tasks=None):
        return bm.BenchmarkResult(
            provider=provider,
            model=model or "fake-model",
            results=[
                bm.GoldenTaskResult(
                    task="arithmetic_simple",
                    provider=provider,
                    model=model or "fake-model",
                    ok=True,
                    latency_ms=5,
                    reply="360",
                    error=None,
                )
            ],
        )

    monkeypatch.setattr("localsmartz.serve.run_golden_on_provider", _fake, raising=False)
    # The handler does ``from localsmartz.benchmarking import ...`` inside
    # the method, so patch at the source module too.
    monkeypatch.setattr(bm, "run_golden_on_provider", _fake)

    status, data = _post_json(
        server, "/api/evals/run", {"provider": "ollama", "model": "fake-model"}
    )
    assert status == 200, data
    assert data["provider"] == "ollama"
    assert data["pass"] == 1
    assert data["fail"] == 0
    assert data["results"][0]["task"] == "arithmetic_simple"


def test_post_evals_run_rejects_non_string_provider(server: int) -> None:
    status, _ = _post_json(server, "/api/evals/run", {"provider": 42})
    assert status == 400


def test_post_evals_run_rejects_empty_provider(server: int) -> None:
    status, _ = _post_json(server, "/api/evals/run", {"provider": ""})
    assert status == 400


# ── Thread pattern pinning (Item 7) ──────────────────────────────────────

def test_research_post_409_on_pattern_mismatch(tmp_path: Path, server: int, monkeypatch) -> None:
    # Create a pinned thread in a tmp workspace.
    from localsmartz import threads as th

    cwd = str(tmp_path)
    th.create_thread("t-pinned", cwd, title="x", pattern="single", provider="ollama")

    status, data = _post_json(
        server,
        "/api/research",
        {
            "prompt": "hi",
            "thread_id": "t-pinned",
            "cwd": cwd,
            "pattern": "critic_loop",
            "provider": "ollama",
        },
    )
    assert status == 409, data
    assert data["error"] == "pattern_mismatch"
    assert data["pinned"]["pattern"] == "single"
    assert data["requested"]["pattern"] == "critic_loop"


def test_check_pattern_unpinned_returns_none(tmp_path: Path) -> None:
    from localsmartz import threads as th

    cwd = str(tmp_path)
    th.create_thread("t-unpinned", cwd, title="x")
    assert th.check_pattern("t-unpinned", cwd, "single", "ollama") is None


def test_check_pattern_matching_returns_none(tmp_path: Path) -> None:
    from localsmartz import threads as th

    cwd = str(tmp_path)
    th.create_thread("t-pinned", cwd, title="x", pattern="single", provider="ollama")
    assert th.check_pattern("t-pinned", cwd, "single", "ollama") is None


def test_check_pattern_provider_mismatch(tmp_path: Path) -> None:
    from localsmartz import threads as th

    cwd = str(tmp_path)
    th.create_thread("t-pinned", cwd, title="x", pattern="single", provider="ollama")
    conflict = th.check_pattern("t-pinned", cwd, "single", "anthropic")
    assert conflict is not None
    assert conflict["error"] == "pattern_mismatch"
    assert conflict["pinned"]["provider"] == "ollama"
    assert conflict["requested"]["provider"] == "anthropic"


# ── Cloud provider dispatch (Item 5) ─────────────────────────────────────

def test_create_model_dispatches_on_provider(tmp_path: Path, monkeypatch) -> None:
    """``agent._create_model`` branches on the ``provider`` config key."""
    from localsmartz import agent as ag

    # Route config.load_config to a tmp cwd with provider=anthropic.
    (tmp_path / ".localsmartz").mkdir()
    (tmp_path / ".localsmartz" / "config.json").write_text(
        '{"planning_model": "qwen3:8b", "provider": "anthropic"}'
    )
    monkeypatch.chdir(tmp_path)

    # Stub the anthropic builder so we don't hit the network / SDK version
    # mismatch in CI.
    called: dict[str, str] = {}

    def _fake_anthropic(name: str):
        called["name"] = name
        return "STUB_ANTHROPIC_MODEL"

    monkeypatch.setattr(ag, "_create_anthropic_model", _fake_anthropic)

    profile = {"name": "full", "planning_model": "claude-sonnet-4-5-20250929"}
    out = ag._create_model(profile, "planning", model_name="claude-sonnet-4-5-20250929")
    assert out == "STUB_ANTHROPIC_MODEL"
    assert called["name"] == "claude-sonnet-4-5-20250929"


def test_create_model_defaults_to_ollama(tmp_path: Path, monkeypatch) -> None:
    """Missing or unknown provider → ollama path."""
    from localsmartz import agent as ag

    (tmp_path / ".localsmartz").mkdir()
    (tmp_path / ".localsmartz" / "config.json").write_text(
        '{"planning_model": "qwen3:8b"}'
    )
    monkeypatch.chdir(tmp_path)

    called: dict[str, str] = {}

    def _fake_ollama(name: str):
        called["name"] = name
        return "STUB_OLLAMA_MODEL"

    monkeypatch.setattr(ag, "_create_ollama_model", _fake_ollama)

    profile = {"name": "full", "planning_model": "qwen3:8b"}
    out = ag._create_model(profile, "planning", model_name="qwen3:8b")
    assert out == "STUB_OLLAMA_MODEL"
    assert called["name"] == "qwen3:8b"


def test_golden_task_grader() -> None:
    from localsmartz.benchmarking import _grade_reply

    assert _grade_reply("The answer is 360.", ["360"]) is True
    assert _grade_reply("The answer is three hundred sixty.", ["360"]) is False
    assert _grade_reply("Red, blue, yellow.", ["red", "blue", "yellow"]) is True
    assert _grade_reply("Red, green.", ["red", "blue", "yellow"]) is False


def test_run_golden_reports_import_error_for_missing_sdk(monkeypatch) -> None:
    """Unknown provider → all tasks errored with a non-None error field."""
    from localsmartz import benchmarking as bm

    result = bm.run_golden_on_provider("not-a-real-provider", model="whatever")
    assert result.pass_count == 0
    assert result.fail_count == len(bm.GOLDEN_TASKS)
    assert all(r.error for r in result.results)


def _get_json(port: int, path: str) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    status = resp.status
    body = resp.read().decode("utf-8")
    conn.close()
    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, {"_raw": body}


def test_pattern_current_defaults(server: int, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    status, data = _get_json(server, "/api/patterns/current")
    assert status == 200
    assert data["pattern"] == "single"
    assert data["provider"] == "ollama"


def test_pattern_active_set_persists(server: int, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    status, data = _post_json(
        server, "/api/patterns/active",
        {"pattern": "critic_loop", "provider": "anthropic"},
    )
    assert status == 200, data
    assert data["ok"] is True
    # Read back.
    status, current = _get_json(server, "/api/patterns/current")
    assert status == 200
    assert current["pattern"] == "critic_loop"
    assert current["provider"] == "anthropic"


def test_pattern_active_rejects_unknown(server: int, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    status, _ = _post_json(
        server, "/api/patterns/active",
        {"pattern": "debate", "provider": "ollama"},
    )
    assert status == 400


def test_diff_results_structure() -> None:
    from localsmartz import benchmarking as bm

    a = bm.BenchmarkResult(
        provider="ollama",
        model="qwen3.5:9b-q4_K_M",
        results=[
            bm.GoldenTaskResult("arithmetic_simple", "ollama", "qwen", True, 100, "360"),
            bm.GoldenTaskResult("capital_city", "ollama", "qwen", True, 90, "paris"),
        ],
    )
    b = bm.BenchmarkResult(
        provider="anthropic",
        model="claude-sonnet",
        results=[
            bm.GoldenTaskResult("arithmetic_simple", "anthropic", "claude", True, 200, "360"),
            bm.GoldenTaskResult("capital_city", "anthropic", "claude", False, 210, "Lyon"),
        ],
    )
    diff = bm.diff_results(a, b)
    assert diff["left"]["provider"] == "ollama"
    assert diff["right"]["provider"] == "anthropic"
    assert "arithmetic_simple" in diff["agree"]
    assert any(d["task"] == "capital_city" for d in diff["disagree"])
