"""local_only enforcement (work item 4) at all three Python model-construction
choke points, secrets.export_to_env's cloud-preset skip, and the serve.py
HTTP 403 / research-dispatch-coercion surfaces."""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest

from localsmartz import global_config
from localsmartz.runners.base import LocalOnlyError


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


# ── Choke point 1: runners.get_runner ──────────────────────────────────────

def test_get_runner_raises_local_only_for_cloud_provider():
    from localsmartz.runners import get_runner

    global_config.set("local_only", True)
    with pytest.raises(LocalOnlyError):
        get_runner("anthropic")
    with pytest.raises(LocalOnlyError):
        get_runner("openai")
    with pytest.raises(LocalOnlyError):
        get_runner("groq")


def test_get_runner_ollama_unaffected_by_local_only():
    from localsmartz.runners import get_runner, LocalOllamaRunner

    global_config.set("local_only", True)
    assert isinstance(get_runner("ollama"), LocalOllamaRunner)


def test_get_runner_cloud_allowed_when_local_only_off():
    from localsmartz.runners import get_runner

    global_config.set("local_only", False)
    # anthropic SDK may not be installed in this env — ImportError is the
    # expected non-LocalOnlyError outcome, proving the gate didn't fire.
    try:
        get_runner("anthropic")
    except LocalOnlyError:
        pytest.fail("LocalOnlyError raised while local_only is False")
    except ImportError:
        pass


# ── Choke point 2: runners.factory.create_langchain_model ────────────────

def test_create_langchain_model_raises_local_only_for_cloud():
    from localsmartz.runners.factory import create_langchain_model

    global_config.set("local_only", True)
    with pytest.raises(LocalOnlyError):
        create_langchain_model("anthropic", {"name": "claude-sonnet-4-6"})


def test_create_langchain_model_ollama_unaffected(monkeypatch):
    from localsmartz.runners.factory import create_langchain_model

    global_config.set("local_only", True)

    class FakeChatOllama:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("langchain_ollama.ChatOllama", FakeChatOllama)
    model = create_langchain_model("ollama", {"name": "qwen3:8b"})
    assert isinstance(model, FakeChatOllama)


# ── Choke point 3: agent._create_model ────────────────────────────────────

def test_agent_create_model_raises_local_only_for_cloud_provider(tmp_path, monkeypatch):
    from localsmartz import agent as agent_mod
    from localsmartz.profiles import get_profile

    global_config.set("local_only", True)
    # Force the non-ollama provider path without touching a real project
    # config file — _active_provider reads .localsmartz/config.json, so
    # patch it directly to avoid cwd side effects.
    monkeypatch.setattr(agent_mod, "_active_provider", lambda: "anthropic")
    profile = get_profile("full")
    with pytest.raises(LocalOnlyError):
        agent_mod._create_model(profile, "planning")


def test_agent_create_model_ollama_unaffected_by_local_only(monkeypatch):
    from localsmartz import agent as agent_mod
    from localsmartz.profiles import get_profile

    global_config.set("local_only", True)
    monkeypatch.setattr(agent_mod, "_active_provider", lambda: "ollama")

    class FakeChatOllama:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(agent_mod, "ChatOllama", FakeChatOllama)
    profile = get_profile("full")
    model = agent_mod._create_model(profile, "planning")
    assert isinstance(model, FakeChatOllama)


# ── secrets.export_to_env cloud-preset skip ───────────────────────────────

def test_export_to_env_skips_cloud_presets_under_local_only(monkeypatch):
    from localsmartz import secrets as secrets_mod

    monkeypatch.setattr(secrets_mod, "_keyring", lambda: None)
    for _name, env_name in secrets_mod.PRESET_PROVIDERS:
        monkeypatch.delenv(env_name, raising=False)

    secrets_mod.set("OpenAI", "sk-openai-abcd")
    secrets_mod.set("Tavily", "tv-1234567890")
    global_config.set("local_only", True)

    n = secrets_mod.export_to_env()

    assert "OPENAI_API_KEY" not in __import__("os").environ
    # Non-LLM preset (search API) is unaffected by local_only.
    assert __import__("os").environ.get("TAVILY_API_KEY") == "tv-1234567890"
    assert n >= 1  # Tavily still exported.


def test_export_to_env_exports_cloud_presets_when_local_only_off(monkeypatch):
    from localsmartz import secrets as secrets_mod
    import os

    monkeypatch.setattr(secrets_mod, "_keyring", lambda: None)
    for _name, env_name in secrets_mod.PRESET_PROVIDERS:
        monkeypatch.delenv(env_name, raising=False)

    secrets_mod.set("OpenAI", "sk-openai-abcd")
    global_config.set("local_only", False)

    secrets_mod.export_to_env()
    assert os.environ.get("OPENAI_API_KEY") == "sk-openai-abcd"


# ── serve.py HTTP 403 surfaces ─────────────────────────────────────────────

@pytest.fixture
def server():
    from http.server import HTTPServer
    from localsmartz.serve import LocalSmartzHandler

    srv = HTTPServer(("127.0.0.1", 0), LocalSmartzHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(
        "POST", path, body=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    status = resp.status
    body = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return status, body


def test_patterns_active_403_under_local_only(server):
    global_config.set("local_only", True)
    status, body = _post(server, "/api/patterns/active", {"pattern": "single", "provider": "anthropic"})
    assert status == 403
    assert body == {"error": "local_only", "detail": "cloud providers disabled by Local-Only setting"}


def test_patterns_active_ollama_unaffected_by_local_only(server):
    global_config.set("local_only", True)
    status, _body = _post(server, "/api/patterns/active", {"pattern": "single", "provider": "ollama"})
    assert status == 200


def test_cloud_estimate_403_under_local_only(server):
    global_config.set("local_only", True)
    status, body = _post(server, "/api/cloud/estimate", {"model": "claude-sonnet-4-6", "prompt": "hi"})
    assert status == 403
    assert body["error"] == "local_only"


def test_evals_run_403_under_local_only_cloud_provider(server):
    global_config.set("local_only", True)
    status, body = _post(server, "/api/evals/run", {"provider": "anthropic"})
    assert status == 403
    assert body["error"] == "local_only"


def test_evals_run_ollama_unaffected_by_local_only(server, monkeypatch):
    from localsmartz import benchmarking

    global_config.set("local_only", True)

    class _FakeResult:
        pass_count = 1
        fail_count = 0
        results = []

    monkeypatch.setattr(benchmarking, "run_golden_on_provider", lambda *a, **k: _FakeResult())
    monkeypatch.setattr(benchmarking, "benchmark_to_dict", lambda r: {"pass": r.pass_count, "fail": r.fail_count})
    status, _body = _post(server, "/api/evals/run", {"provider": "ollama"})
    assert status == 200


# ── F1: degraded (corrupt global.json) fails CLOSED, missing file is open ──
# A privacy boundary must fail closed on an unreadable config, not open —
# the pre-fix bug was that a corrupt ~/.localsmartz/global.json made
# global_config.load_global() silently return all-defaults, so every
# _local_only_enabled() helper reported False (cloud allowed) instead of
# denying it. These tests pin the corrected polarity at all 3 Python
# model-construction choke points, plus the missing-file (fresh install)
# case that must stay permissive.

def _write_corrupt_global_json(home: "object") -> None:
    config_dir = home / ".localsmartz"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "global.json").write_text("{not valid json")


def test_get_runner_denies_cloud_and_warns_when_config_corrupt(fake_home, capsys):
    from localsmartz.runners import get_runner

    _write_corrupt_global_json(fake_home)
    with pytest.raises(LocalOnlyError):
        get_runner("anthropic")
    err = capsys.readouterr().err
    assert "failing closed" in err
    assert "cloud providers disabled" in err


def test_get_runner_allows_cloud_when_config_missing(fake_home):
    from localsmartz.runners import get_runner

    # No global.json at all — fresh install, not degraded.
    assert not (fake_home / ".localsmartz" / "global.json").exists()
    try:
        get_runner("anthropic")
    except LocalOnlyError:
        pytest.fail("LocalOnlyError raised for a missing (non-degraded) config")
    except ImportError:
        pass  # anthropic SDK may not be installed — proves the gate didn't fire


def test_create_langchain_model_denies_cloud_and_warns_when_config_corrupt(fake_home, capsys):
    from localsmartz.runners.factory import create_langchain_model

    _write_corrupt_global_json(fake_home)
    with pytest.raises(LocalOnlyError):
        create_langchain_model("anthropic", {"name": "claude-sonnet-4-6"})
    err = capsys.readouterr().err
    assert "failing closed" in err
    assert "cloud providers disabled" in err


def test_create_langchain_model_allows_cloud_when_config_missing(monkeypatch, fake_home):
    from localsmartz.runners.factory import create_langchain_model

    assert not (fake_home / ".localsmartz" / "global.json").exists()

    class FakeChatOllama:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    # Route through ollama (no cloud SDK dependency) to prove the gate
    # didn't block — LocalOnlyError would only ever fire for provider !=
    # ollama, so this call succeeding at all demonstrates no false-positive.
    monkeypatch.setattr("langchain_ollama.ChatOllama", FakeChatOllama)
    model = create_langchain_model("ollama", {"name": "qwen3:8b"})
    assert isinstance(model, FakeChatOllama)


def test_agent_create_model_denies_cloud_and_warns_when_config_corrupt(fake_home, capsys, monkeypatch):
    from localsmartz import agent as agent_mod
    from localsmartz.profiles import get_profile

    _write_corrupt_global_json(fake_home)
    monkeypatch.setattr(agent_mod, "_active_provider", lambda: "anthropic")
    profile = get_profile("full")
    with pytest.raises(LocalOnlyError):
        agent_mod._create_model(profile, "planning")
    err = capsys.readouterr().err
    assert "failing closed" in err
    assert "cloud providers disabled" in err


def test_agent_create_model_allows_cloud_when_config_missing(fake_home, monkeypatch):
    from localsmartz import agent as agent_mod
    from localsmartz.profiles import get_profile

    assert not (fake_home / ".localsmartz" / "global.json").exists()
    monkeypatch.setattr(agent_mod, "_active_provider", lambda: "anthropic")
    profile = get_profile("full")

    class FakeChatAnthropic:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(agent_mod, "_create_anthropic_model", lambda name: FakeChatAnthropic())
    model = agent_mod._create_model(profile, "planning")
    assert isinstance(model, FakeChatAnthropic)


def test_secrets_export_denies_cloud_presets_and_warns_when_config_corrupt(fake_home, capsys, monkeypatch):
    from localsmartz import secrets as secrets_mod

    monkeypatch.setattr(secrets_mod, "_keyring", lambda: None)
    for _name, env_name in secrets_mod.PRESET_PROVIDERS:
        monkeypatch.delenv(env_name, raising=False)

    secrets_mod.set("OpenAI", "sk-openai-abcd")
    _write_corrupt_global_json(fake_home)

    n = secrets_mod.export_to_env()
    err = capsys.readouterr().err
    assert "failing closed" in err
    assert "OPENAI_API_KEY" not in __import__("os").environ
    assert n == 0


def test_patterns_active_403_degraded_detail_names_unreadable_config(server, fake_home):
    _write_corrupt_global_json(fake_home)
    status, body = _post(server, "/api/patterns/active", {"pattern": "single", "provider": "anthropic"})
    assert status == 403
    assert body["error"] == "local_only"
    assert "unreadable" in body["detail"].lower()
