from __future__ import annotations

from types import SimpleNamespace

from localsmartz.routing import (
    is_coding_intent,
    is_coding_loop_intent,
    select_research_runtime,
)


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


def test_coding_intent_detects_airplane_harness_prompt():
    prompt = (
        "how can we use localsmartz as a coding harness, can we use some "
        "of the on device plugins and /Users/tyroneross/dev/git-folder/build-loop"
    )
    assert is_coding_intent(prompt) is True
    assert is_coding_loop_intent(prompt) is False
    assert select_research_runtime(prompt) == "coding_harness"


def test_coding_intent_is_typo_tolerant_for_harness_prompt():
    prompt = (
        "What di yiy beed ti vyukd tge cidubg garbess, "
        "I want a harnes with model selecto from ollama or other local models"
    )
    assert is_coding_intent(prompt) is True
    assert is_coding_loop_intent(prompt) is False
    assert select_research_runtime(prompt) == "coding_harness"


def test_action_coding_intent_uses_coding_loop():
    prompt = "implement a model selector for the local coding harness"
    assert is_coding_intent(prompt) is True
    assert is_coding_loop_intent(prompt) is True
    assert select_research_runtime(prompt) == "coding_loop"


def test_nextjs_persona_planning_prompt_uses_coding_harness():
    prompt = (
        "In this Next.js persona app, propose a small implementation plan for "
        "a competitive research screen."
    )
    assert is_coding_intent(prompt) is True
    assert is_coding_loop_intent(prompt) is False
    assert select_research_runtime(prompt) == "coding_harness"


def test_nextjs_persona_build_prompt_uses_coding_loop():
    prompt = (
        "Build a competitive research screen in this Next.js persona app and "
        "add fixture personas."
    )
    assert is_coding_intent(prompt) is True
    assert is_coding_loop_intent(prompt) is True
    assert select_research_runtime(prompt) == "coding_loop"


def test_generic_model_selection_stays_research_graph(monkeypatch):
    monkeypatch.setattr("localsmartz.profiles.is_fast_path", lambda _prompt: False)
    monkeypatch.setattr("localsmartz.pipeline.is_enabled", lambda: True)
    assert (
        select_research_runtime("which model should I use for local agents, qwen or gpt-oss?")
        == "graph_pipeline"
    )


def test_climate_report_does_not_match_cli_token(monkeypatch):
    monkeypatch.setattr("localsmartz.profiles.is_fast_path", lambda _prompt: False)
    monkeypatch.setattr("localsmartz.pipeline.is_enabled", lambda: True)
    assert is_coding_intent("write a report on climate change") is False
    assert select_research_runtime("write a report on climate change") == "graph_pipeline"


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

    monkeypatch.setattr(main_mod, "_preflight", lambda _profile, **_kwargs: True)
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


def test_cli_run_uses_plain_status_without_quality_review(monkeypatch, tmp_path, capsys):
    from localsmartz import __main__ as main_mod

    fake_profile = {
        "name": "full",
        "planning_model": "gpt-oss:20b",
        "execution_model": "qwen2.5-coder:32b-instruct-q5_K_M",
        "max_turns": 5,
    }

    def fail_review(*_args, **_kwargs):
        raise AssertionError("review_output should be opt-in for normal CLI use")

    monkeypatch.delenv("LOCALSMARTZ_CLI_QUALITY_REVIEW", raising=False)
    monkeypatch.setattr(main_mod, "_preflight", lambda _profile, **_kwargs: True)
    monkeypatch.setattr("localsmartz.config.resolve_model", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "localsmartz.profiles.get_profile",
        lambda *_a, **_k: fake_profile,
    )
    monkeypatch.setattr(
        "localsmartz.routing.select_research_runtime",
        lambda *_a, **_k: "fast_path",
    )
    monkeypatch.setattr(
        main_mod,
        "_run_fast_path_cli",
        lambda *_a, **_k: "direct answer",
    )
    monkeypatch.setattr("localsmartz.agent.review_output", fail_review)

    args = SimpleNamespace(quiet=False, thread=None, profile="full", model=None)
    main_mod._run("what is 2+2?", args, tmp_path)

    captured = capsys.readouterr()
    assert "direct answer" in captured.out
    assert "Answering directly because this looks simple." in captured.err
    assert "Route:" not in captured.err
    assert "fast_path" not in captured.err
    assert "Quality Review" not in captured.err


def test_cli_stage_output_uses_plain_language(monkeypatch, tmp_path, capsys):
    from localsmartz import __main__ as main_mod

    fake_profile = {
        "name": "lite",
        "planning_model": "qwen3:8b-q4_K_M",
        "execution_model": "qwen3:8b-q4_K_M",
        "max_turns": 5,
    }

    def fake_graph_run(prompt, profile=None, sink=None, with_agents=False):
        if sink is not None:
            sink({"type": "stage", "stage": "writer"})
        return {"final_answer": "graph answer", "messages": []}

    monkeypatch.setattr(main_mod, "_preflight", lambda _profile, **_kwargs: True)
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

    args = SimpleNamespace(quiet=False, thread=None, profile="lite", model=None)
    main_mod._run("research the market", args, tmp_path)

    captured = capsys.readouterr()
    assert "graph answer" in captured.out
    assert "Researching with local tools" in captured.err
    assert "Writing the answer." in captured.err
    assert "▸ writer" not in captured.err
    assert "graph_pipeline" not in captured.err


def test_cli_run_uses_coding_harness(monkeypatch, tmp_path, capsys):
    from localsmartz import __main__ as main_mod

    fake_profile = {
        "name": "full",
        "planning_model": "gpt-oss:20b",
        "execution_model": "qwen2.5-coder:32b-instruct-q5_K_M",
        "max_turns": 5,
    }
    calls = {"coding": 0, "graph": 0, "full_agent": 0}
    preflight_models: list[str] = []

    def fake_coding(prompt, profile, *, cwd, model_override, verbose):
        calls["coding"] += 1
        assert "coding harness" in prompt
        assert cwd == tmp_path
        assert model_override is None
        assert profile["planning_model"] == "qwen2.5-coder:32b-instruct-q5_K_M"
        return "coding answer"

    def fake_graph_run(*args, **kwargs):
        calls["graph"] += 1
        return {"final_answer": "graph answer", "messages": []}

    def fake_run_research(*args, **kwargs):
        calls["full_agent"] += 1
        return {"messages": []}

    def fake_preflight(profile, **_kwargs):
        preflight_models.append(profile["planning_model"])
        return True

    monkeypatch.setattr(main_mod, "_preflight", fake_preflight)
    monkeypatch.setattr("localsmartz.config.resolve_model", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "localsmartz.profiles.get_profile",
        lambda *_a, **_k: fake_profile.copy(),
    )
    monkeypatch.setattr(
        "localsmartz.routing.select_research_runtime",
        lambda *_a, **_k: "coding_harness",
    )
    monkeypatch.setattr(main_mod, "_run_coding_harness_cli", fake_coding)
    monkeypatch.setattr("localsmartz.pipeline.run", fake_graph_run)
    monkeypatch.setattr("localsmartz.agent.run_research", fake_run_research)
    monkeypatch.setattr("localsmartz.agent.review_output", lambda *_a, **_k: None)

    args = SimpleNamespace(quiet=True, thread=None, profile="full", model=None)
    main_mod._run("how can we use localsmartz as a coding harness", args, tmp_path)

    captured = capsys.readouterr()
    assert "coding answer" in captured.out
    assert preflight_models == ["qwen2.5-coder:32b-instruct-q5_K_M"]
    assert calls["coding"] == 1
    assert calls["graph"] == 0
    assert calls["full_agent"] == 0


def test_cli_run_uses_coding_loop(monkeypatch, tmp_path, capsys):
    from localsmartz import __main__ as main_mod

    fake_profile = {
        "name": "full",
        "planning_model": "gpt-oss:20b",
        "execution_model": "qwen2.5-coder:32b-instruct-q5_K_M",
        "max_turns": 5,
    }
    calls = {"coding_loop": 0, "graph": 0, "full_agent": 0}
    preflight_models: list[str] = []

    def fake_coding_loop(prompt, profile, *, cwd, model_override, verbose):
        calls["coding_loop"] += 1
        assert "model selector" in prompt
        assert cwd == tmp_path
        assert model_override is None
        assert profile["planning_model"] == "qwen2.5-coder:32b-instruct-q5_K_M"
        return "coding loop answer"

    def fake_graph_run(*args, **kwargs):
        calls["graph"] += 1
        return {"final_answer": "graph answer", "messages": []}

    def fake_run_research(*args, **kwargs):
        calls["full_agent"] += 1
        return {"messages": []}

    def fake_preflight(profile, **_kwargs):
        preflight_models.append(profile["planning_model"])
        return True

    monkeypatch.setattr(main_mod, "_preflight", fake_preflight)
    monkeypatch.setattr("localsmartz.config.resolve_model", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "localsmartz.profiles.get_profile",
        lambda *_a, **_k: fake_profile.copy(),
    )
    monkeypatch.setattr(
        "localsmartz.routing.select_research_runtime",
        lambda *_a, **_k: "coding_loop",
    )
    monkeypatch.setattr(main_mod, "_run_coding_loop_cli", fake_coding_loop)
    monkeypatch.setattr("localsmartz.pipeline.run", fake_graph_run)
    monkeypatch.setattr("localsmartz.agent.run_research", fake_run_research)
    monkeypatch.setattr("localsmartz.agent.review_output", lambda *_a, **_k: None)

    args = SimpleNamespace(quiet=True, thread=None, profile="full", model=None)
    main_mod._run("implement a model selector for the local coding harness", args, tmp_path)

    captured = capsys.readouterr()
    assert "coding loop answer" in captured.out
    assert preflight_models == ["qwen2.5-coder:32b-instruct-q5_K_M"]
    assert calls["coding_loop"] == 1
    assert calls["graph"] == 0
    assert calls["full_agent"] == 0
