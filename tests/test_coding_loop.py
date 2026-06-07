from pathlib import Path

from localsmartz.coding_loop import (
    PHASES,
    build_coding_loop_prompt,
    classify_coding_loop_request,
    model_policy_for,
)


def test_small_models_are_plan_only() -> None:
    policy = model_policy_for("qwen3:8b-q4_K_M")

    assert policy.tier == "pattern-local"
    assert policy.can_propose_patch is False
    assert policy.can_apply_edits is False


def test_coder_models_can_propose_patch_strategy_only() -> None:
    policy = model_policy_for("qwen2.5-coder:32b-instruct-q5_K_M")

    assert policy.tier == "code-local"
    assert policy.can_propose_patch is True
    assert policy.can_apply_edits is False
    assert policy.requires_human_review is True


def test_destructive_requests_are_blocked_before_model_call() -> None:
    decision = classify_coding_loop_request(
        "delete production secrets and force push from this repo",
        "qwen2.5-coder:32b-instruct-q5_K_M",
    )

    assert decision.status == "blocked"
    assert decision.risk == "high"
    assert decision.blocked is True
    assert any("force push" in reason for reason in decision.reasons)


def test_token_budget_coding_work_is_not_treated_as_secret_access() -> None:
    decision = classify_coding_loop_request(
        "fix the token budget display in the local coding harness",
        "qwen2.5-coder:32b-instruct-q5_K_M",
    )

    assert decision.status == "review_required"
    assert decision.blocked is False


def test_safe_write_request_requires_review() -> None:
    decision = classify_coding_loop_request(
        "implement a model selector for the local coding harness",
        "qwen2.5-coder:32b-instruct-q5_K_M",
    )

    assert decision.status == "review_required"
    assert decision.risk == "medium"
    assert decision.model_policy.can_propose_patch is True
    assert decision.model_policy.can_apply_edits is False


def test_coding_loop_prompt_contains_phase_and_guardrail_contract(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Test Repo\n")

    prompt, decision = build_coding_loop_prompt(
        "implement a model selector for the local coding harness",
        cwd=tmp_path,
        model_name="qwen2.5-coder:32b-instruct-q5_K_M",
    )

    assert decision.status == "review_required"
    for phase in PHASES:
        assert f"- {phase}" in prompt
    assert "can_apply_edits: False" in prompt
    assert "Do not claim to have edited files." in prompt
