"""Tests that /api/evals/run picks an installed model rather than the
hardcoded catalog default when no model is supplied."""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import HTTPServer
from unittest.mock import MagicMock, patch

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


def _post(port: int, path: str, payload: dict) -> tuple[int, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload)
    conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    status = resp.status
    data = json.loads(resp.read().decode("utf-8"))
    conn.close()
    return status, data


# ---------------------------------------------------------------------------
# Unit-level: test the model-selection logic directly (no HTTP overhead)
# ---------------------------------------------------------------------------

class TestEvalsDefaultModelResolution:
    """Verify run_golden_on_provider picks the right model when model=None."""

    def _run_with_installed(self, installed_models: list[str]) -> str:
        """Call run_golden_on_provider with a patched installed-model list and
        an empty task list; return the resolved model name."""
        from localsmartz.benchmarking import run_golden_on_provider, GoldenTask

        # One trivial task so the runner is called but we can stub it out
        stub_task = GoldenTask(
            name="stub",
            prompt="Say 'ok'",
            must_contain=["ok"],
        )

        captured: dict = {}

        def fake_run_golden(provider: str, *, model: str | None = None, tasks=None):
            # Capture what model was resolved before we intercept
            pass

        # We need to intercept *after* model resolution but before actual LLM call.
        # Patch the runner to capture the model_ref used.
        class _CapturingRunner:
            async def run(self, messages, *, model, **kw):
                captured["model"] = model["name"]
                return "ok"

        with (
            patch("localsmartz.ollama.list_models", return_value=installed_models),
            patch("localsmartz.benchmarking._list_ollama", return_value=installed_models, create=True),
            patch("localsmartz.runners.get_runner", return_value=_CapturingRunner()),
        ):
            # Re-import inside patch context so the function re-evaluates
            import localsmartz.benchmarking as bm
            import importlib
            # Patch at the module level where the function does its import
            with patch.object(bm, "_list_ollama" if hasattr(bm, "_list_ollama") else "__builtins__",
                               return_value=installed_models, create=True):
                pass  # no-op; we'll patch the ollama module directly

            # Patch localsmartz.ollama.list_models which benchmarking imports as _list_ollama
            with patch("localsmartz.ollama.list_models", return_value=installed_models):
                result = run_golden_on_provider("ollama", model=None, tasks=[stub_task])

        return result.model

    def test_picks_installed_model_not_hardcoded_default(self):
        """When only qwen3:8b-q4_K_M is installed, that model is used."""
        installed = ["qwen3:8b-q4_K_M"]
        resolved = self._run_with_installed(installed)
        assert resolved == "qwen3:8b-q4_K_M", (
            f"Expected 'qwen3:8b-q4_K_M', got '{resolved}'"
        )

    def test_does_not_use_hardcoded_default_when_different_model_installed(self):
        """The hardcoded default qwen3.5:9b-q4_K_M must NOT be chosen when
        it is not present in the installed list."""
        installed = ["qwen3:8b-q4_K_M"]
        resolved = self._run_with_installed(installed)
        assert resolved != "qwen3.5:9b-q4_K_M", (
            "Handler used hardcoded default even though it is not installed"
        )

    def test_explicit_model_arg_is_respected(self):
        """If the caller passes an explicit model, it wins regardless of what
        is installed."""
        from localsmartz.benchmarking import run_golden_on_provider, GoldenTask

        stub_task = GoldenTask(name="stub", prompt="Say 'ok'", must_contain=["ok"])

        class _CapturingRunner:
            async def run(self, messages, *, model, **kw):
                return "ok"

        with (
            patch("localsmartz.ollama.list_models", return_value=["qwen3:8b-q4_K_M"]),
            patch("localsmartz.runners.get_runner", return_value=_CapturingRunner()),
        ):
            result = run_golden_on_provider(
                "ollama", model="my-custom-model:7b", tasks=[stub_task]
            )

        assert result.model == "my-custom-model:7b"
