from __future__ import annotations

from types import SimpleNamespace

from localsmartz.routing import select_research_runtime


def test_select_research_runtime_prefers_fast_path(monkeypatch):
    monkeypatch.setattr("localsmartz.profiles.is_fast_path", lambda _prompt: True)
    monkeypatch.setattr("localsmartz.pipeline.is_enabled", lambda: True)
    assert select_research_runtime("what is 2+2?") == "fast_path"


def test_select_research_runtime_uses_graph_by_default(monkeypatch):
    monkeypatch.setattr("localsmartz.profiles.is_fast_path", lambda _prompt: False)
    monkeypatch.setattr("localsmartz.pipeline.is_enabled", lambda: True)
    assert select_research_runtime("research the market") == "graph_pipeline"


def test_select_research_runtime_focus_mode_bypasses_graph(monkeypatch):
    monkeypatch.setattr("localsmartz.profiles.is_fast_path", lambda _prompt: True)
    monkeypatch.setattr("localsmartz.pipeline.is_enabled", lambda: True)
    assert (
        select_research_runtime("what is 2+2?", focus_agent="researcher")
        == "full_agent"
    )


def test_select_research_runtime_respects_graph_disable(monkeypatch):
    monkeypatch.setattr("localsmartz.profiles.is_fast_path", lambda _prompt: False)
    monkeypatch.setattr("localsmartz.pipeline.is_enabled", lambda: False)
    assert select_research_runtime("research the market") == "full_agent"


def test_cli_run_uses_graph_pipeline(monkeypatch, tmp_path, capsys):
    from localsmartz import __main__ as main_mod

    fake_profile = {
        "name": "lite",
        "planning_model": "qwen3:8b-q4_K_M",
        "execution_model": "qwen3:8b-q4_K_M",
        "max_turns": 5,
    }
    calls = {"graph": 0, "full_agent": 0}

    def fake_graph_run(prompt, profile=None, sink=None, with_agents=False):
        calls["graph"] += 1
        assert prompt == "research the market"
        assert profile == fake_profile
        assert with_agents is True
        if sink is not None:
            sink({"type": "stage", "stage": "writer"})
        return {"final_answer": "graph answer", "messages": []}

    def fake_run_research(*args, **kwargs):
        calls["full_agent"] += 1
        return {"messages": []}

    monkeypatch.setattr(main_mod, "_preflight", lambda _profile: True)
    monkeypatch.setattr("localsmartz.config.resolve_model", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "localsmartz.profiles.get_profile",
        lambda *_a, **_k: fake_profile,
    )
    monkeypatch.setattr(
        "localsmartz.routing.select_research_runtime",
        lambda *_a, **_k: "graph_pipeline",
    )
    monkeypatch.setattr("localsmartz.pipeline.run", fake_graph_run)
    monkeypatch.setattr("localsmartz.agent.run_research", fake_run_research)

    args = SimpleNamespace(quiet=True, thread=None, profile="lite", model=None)
    main_mod._run("research the market", args, tmp_path)

    captured = capsys.readouterr()
    assert "graph answer" in captured.out
    assert calls["graph"] == 1
    assert calls["full_agent"] == 0
