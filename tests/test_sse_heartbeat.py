"""Tests for the SSE heartbeat pulse + tool-name validator + warmup state."""

import threading
import time

from localsmartz.serve import (
    _HeartbeatPulse,
    _WARMUP_STATE,
    _WARMUP_STATE_LOCK,
    _is_valid_tool_name,
    _warmup_in_background,
)


# ── Tool name validator ────────────────────────────────────────────────

def test_tool_name_validator_accepts_plain_names():
    assert _is_valid_tool_name("write_todos") is True
    assert _is_valid_tool_name("web_search") is True
    assert _is_valid_tool_name("python_exec") is True


def test_tool_name_validator_rejects_dotted_namespace():
    """qwen3:8b hallucinates things like 'repo_browser.write_todos' under
    long prompts. The validator rejects these before they reach the UI."""
    assert _is_valid_tool_name("repo_browser.write_todos") is False
    assert _is_valid_tool_name("mcp.some_tool") is False


def test_tool_name_validator_rejects_slashes():
    assert _is_valid_tool_name("namespace/tool") is False


def test_tool_name_validator_rejects_empty_and_nonstring():
    assert _is_valid_tool_name("") is False
    assert _is_valid_tool_name(None) is False  # type: ignore[arg-type]
    assert _is_valid_tool_name(123) is False  # type: ignore[arg-type]


def test_tool_name_validator_with_whitelist():
    """If a whitelist is passed, names outside it are also rejected even
    without dots. Guards against the model calling real-looking but
    unbound tools."""
    whitelist = {"write_todos", "ls"}
    assert _is_valid_tool_name("write_todos", valid_names=whitelist) is True
    assert _is_valid_tool_name("unknown_tool", valid_names=whitelist) is False


# ── Heartbeat pulse ────────────────────────────────────────────────────

def test_heartbeat_fires_when_idle():
    """With interval_s=0.1 and no touches, the pulse should emit heartbeats."""
    events: list[dict] = []

    def send(e: dict) -> None:
        events.append(e)

    pulse = _HeartbeatPulse(send, interval_s=0.1)
    pulse.start()
    # Sleep long enough for 2+ heartbeats.
    time.sleep(0.35)
    pulse.stop()

    beats = [e for e in events if e.get("type") == "heartbeat"]
    assert len(beats) >= 2
    assert all(b.get("elapsed_s") is not None for b in beats)


def test_heartbeat_silenced_by_touch():
    """Calling touch() frequently should keep the pulse quiet."""
    events: list[dict] = []

    def send(e: dict) -> None:
        events.append(e)

    pulse = _HeartbeatPulse(send, interval_s=0.5)
    pulse.start()
    # Touch every 50ms for 400ms — stays well under interval.
    for _ in range(8):
        pulse.touch()
        time.sleep(0.05)
    pulse.stop()

    beats = [e for e in events if e.get("type") == "heartbeat"]
    assert beats == []


def test_heartbeat_stops_on_stop():
    """After stop(), no further heartbeats are emitted."""
    events: list[dict] = []

    def send(e: dict) -> None:
        events.append(e)

    pulse = _HeartbeatPulse(send, interval_s=0.1)
    pulse.start()
    time.sleep(0.15)  # Let at least one fire.
    pulse.stop()
    before = len(events)
    time.sleep(0.25)
    assert len(events) == before  # No new events after stop.


# ── Warmup state ───────────────────────────────────────────────────────

def _reset_warmup_state() -> None:
    with _WARMUP_STATE_LOCK:
        _WARMUP_STATE.clear()


def test_warmup_in_background_sets_loading_state(monkeypatch):
    _reset_warmup_state()
    started = threading.Event()
    finished = threading.Event()

    def slow_warmup(model: str, keep_alive: str = "30m", timeout: float = 300.0):
        started.set()
        # Give the test time to observe the 'loading' stage.
        time.sleep(0.1)
        finished.set()
        return True, 100, None

    monkeypatch.setattr("localsmartz.serve.warmup_model", slow_warmup, raising=False)
    # warmup_model is imported lazily inside _warmup_in_background, so also
    # patch the source module.
    import localsmartz.ollama as ollama_mod
    monkeypatch.setattr(ollama_mod, "warmup_model", slow_warmup, raising=True)

    _warmup_in_background("qwen3:8b-q4_K_M")
    assert started.wait(timeout=1.0)
    with _WARMUP_STATE_LOCK:
        state = dict(_WARMUP_STATE["qwen3:8b-q4_K_M"])
    assert state["stage"] == "loading"

    assert finished.wait(timeout=2.0)
    # Poll until the background thread swaps state to 'ready'.
    deadline = time.time() + 1.0
    while time.time() < deadline:
        with _WARMUP_STATE_LOCK:
            stage = _WARMUP_STATE["qwen3:8b-q4_K_M"].get("stage")
        if stage == "ready":
            break
        time.sleep(0.02)
    with _WARMUP_STATE_LOCK:
        assert _WARMUP_STATE["qwen3:8b-q4_K_M"]["stage"] == "ready"


def test_warmup_records_error_state(monkeypatch):
    _reset_warmup_state()

    import localsmartz.ollama as ollama_mod
    monkeypatch.setattr(
        ollama_mod,
        "warmup_model",
        lambda *a, **k: (False, 5, "connection refused"),
        raising=True,
    )

    _warmup_in_background("bogus:model")
    # Wait for the background thread to finish.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        with _WARMUP_STATE_LOCK:
            stage = _WARMUP_STATE.get("bogus:model", {}).get("stage")
        if stage in ("ready", "error"):
            break
        time.sleep(0.02)
    with _WARMUP_STATE_LOCK:
        state = dict(_WARMUP_STATE["bogus:model"])
    assert state["stage"] == "error"
    assert state["error"] == "connection refused"


def test_warmup_deduplicates_in_flight(monkeypatch):
    """A second warmup call for a model that's already loading should be a noop."""
    _reset_warmup_state()
    call_count = {"n": 0}
    can_finish = threading.Event()

    def slow(model: str, keep_alive: str = "30m", timeout: float = 300.0):
        call_count["n"] += 1
        can_finish.wait(timeout=2.0)
        return True, 10, None

    import localsmartz.ollama as ollama_mod
    monkeypatch.setattr(ollama_mod, "warmup_model", slow, raising=True)

    _warmup_in_background("dupe:model")
    # Let the first call land in the state machine.
    time.sleep(0.05)
    _warmup_in_background("dupe:model")  # should be ignored
    _warmup_in_background("dupe:model")  # should be ignored
    can_finish.set()
    # Let the threads finish.
    time.sleep(0.2)
    assert call_count["n"] == 1
