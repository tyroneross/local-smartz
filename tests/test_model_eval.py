from __future__ import annotations

import pytest

from localsmartz.model_eval import (
    ModelEvalTask,
    default_local_eval_models,
    matrix_to_dict,
    run_model_eval,
)


class FakeRunner:
    def __init__(self, replies: dict[str, str]):
        self.replies = replies
        self.calls: list[dict] = []

    async def run_turn(self, prompt: str, *, model_ref, system=None, **_kwargs):
        self.calls.append({"prompt": prompt, "model_ref": model_ref, "system": system})
        return {"content": self.replies[prompt]}


def test_default_local_eval_models_prefers_gpt_oss_and_qwen():
    installed = [
        "nomic-embed-text:latest",
        "qwen3:8b-q4_K_M",
        "gpt-oss:20b",
        "llama3.2:3b",
    ]

    assert default_local_eval_models(installed, limit=2) == [
        "gpt-oss:20b",
        "qwen3:8b-q4_K_M",
    ]


def test_default_local_eval_models_skips_embeddings_when_falling_back():
    installed = ["nomic-embed-text:latest", "llama3.2:3b"]

    assert default_local_eval_models(installed, limit=1) == ["llama3.2:3b"]


def test_run_model_eval_scores_replies_and_records_latency():
    task_ok = ModelEvalTask("ok", "say ok", ("ok",), weight=2.0)
    task_fail = ModelEvalTask("fail", "say no", ("yes",), weight=1.0)
    runner = FakeRunner({"say ok": "ok", "say no": "no"})

    result = run_model_eval(
        model="test-model",
        tasks=[task_ok, task_fail],
        runner=runner,
    )

    assert result.pass_count == 1
    assert result.fail_count == 1
    assert result.weighted_score == pytest.approx(2 / 3)
    assert result.median_latency_ms is not None
    assert runner.calls[0]["model_ref"] == {"provider": "ollama", "name": "test-model"}


def test_matrix_to_dict_ranks_score_before_latency():
    fast_fail = run_model_eval(
        model="fast-fail",
        tasks=[ModelEvalTask("t", "p", ("yes",))],
        runner=FakeRunner({"p": "no"}),
    )
    slow_pass = run_model_eval(
        model="slow-pass",
        tasks=[ModelEvalTask("t", "p", ("yes",))],
        runner=FakeRunner({"p": "yes"}),
    )

    payload = matrix_to_dict([fast_fail, slow_pass])

    assert payload["ranking"][0]["model"] == "slow-pass"
