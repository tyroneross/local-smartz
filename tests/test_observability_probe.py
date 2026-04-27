"""Phoenix probe tests — exercises probe_collector without requiring Phoenix."""
from __future__ import annotations

from localsmartz import observability


def test_probe_collector_returns_false_on_no_daemon(monkeypatch) -> None:
    """No Phoenix running anywhere → probe must return False, not raise."""
    # httpx is imported inside probe_collector; monkeypatching the import is
    # fragile, so we short-circuit via env by pointing at a black-hole URL.
    monkeypatch.setattr(
        observability,
        "DEFAULT_OTLP_ENDPOINT",
        "http://127.0.0.1:59999/v1/traces",
    )
    assert observability.probe_collector() is False


def test_auto_setup_respects_opt_out(monkeypatch) -> None:
    monkeypatch.setenv("LOCALSMARTZ_OBSERVE", "0")
    # probe_collector would say False anyway, but opt-out must win regardless.
    assert observability.auto_setup_if_reachable() is False


def test_status_includes_collector_reachable_field() -> None:
    s = observability.status()
    assert "collector_reachable" in s
    assert isinstance(s["collector_reachable"], bool)
