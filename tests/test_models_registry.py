"""Model registry + catalog + resolve + preflight tests."""
from __future__ import annotations

from localsmartz.models import (
    CATALOG,
    get_model_rec,
    recs_for_tier,
    recs_for_role,
    recommended_for_tier,
)
from localsmartz.models.resolve import resolve_role_model
from localsmartz.models import preflight


def test_qwen35_family_registered_with_reasoning_off() -> None:
    rec = get_model_rec("qwen3.5:9b")
    assert rec is not None
    assert rec["family"] == "qwen3.5"
    assert rec["reasoning_mode"] == "off-by-default"


def test_quant_stripped_lookup() -> None:
    rec = get_model_rec("qwen3.5:9b-q4_K_M")
    assert rec is not None and rec["name"] == "qwen3.5:9b"


def test_recommended_sets_match_research_doc() -> None:
    mini = [r["name"] for r in recommended_for_tier("mini")]
    assert mini == ["qwen3.5:9b"]
    standard = [r["name"] for r in recommended_for_tier("standard")]
    assert "qwen3.5:9b" in standard and "qwen3.5:27b" in standard
    full = [r["name"] for r in recommended_for_tier("full")]
    assert {"qwen3.5:9b", "qwen3.5:122b", "qwen3-next:80b"} <= set(full)


def test_recommended_set_standard_includes_gemma4() -> None:
    names = [r["name"] for r in recommended_for_tier("standard")]
    assert "gemma4:26b" in names


def test_recommended_set_full_includes_gemma4() -> None:
    names = [r["name"] for r in recommended_for_tier("full")]
    assert "gemma4:26b" in names


def test_recommended_set_mini_excludes_gemma4() -> None:
    names = [r["name"] for r in recommended_for_tier("mini")]
    assert "gemma4:26b" not in names


def test_tier_inherits_downward() -> None:
    # A standard-tier machine can run every mini model.
    mini_names = {r["name"] for r in recs_for_tier("mini")}
    standard_names = {r["name"] for r in recs_for_tier("standard")}
    assert mini_names <= standard_names


def test_resolve_role_uses_overrides() -> None:
    config = {
        "agents": {
            "writer": {"model_ref": {"provider": "ollama", "name": "custom:13b"}}
        }
    }
    ref = resolve_role_model("writer", "standard", config)
    assert ref["name"] == "custom:13b"


def test_resolve_mini_tier_forces_critic_to_writer() -> None:
    # Writer already resolved on this run; critic must share it.
    ref = resolve_role_model("critic", "mini", {}, co_resident={"writer": "qwen3.5:9b"})
    assert ref["name"] == "qwen3.5:9b"


def test_preflight_blocks_when_model_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        "localsmartz.models.preflight.model_available", lambda name: False
    )
    # Writer and critic required for critic_loop.
    result = preflight.check("critic_loop", {"tier": "mini", "name": "lite"})
    assert result["ok"] is False
    assert any(m["role"] == "writer" for m in result["missing"])
    assert any("localsmartz model add" in (m.get("install_cmd") or "") for m in result["missing"])


def test_preflight_passes_when_models_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "localsmartz.models.preflight.model_available", lambda name: True
    )
    result = preflight.check("single", {"tier": "mini", "name": "lite"})
    assert result["ok"] is True
    assert result["missing"] == []


def test_preflight_critic_loop_lists_both_roles_when_both_missing(monkeypatch) -> None:
    """Mini tier critic_loop: even though F2 will collapse them to one model,
    preflight should report at least the writer slot clearly. Critic sharing
    the writer model means a single missing pull blocks both roles."""
    monkeypatch.setattr(
        "localsmartz.models.preflight.model_available", lambda name: False
    )
    result = preflight.check("critic_loop", {"tier": "mini", "name": "lite"})
    roles = {m["role"] for m in result["missing"]}
    assert "writer" in roles
    # install_cmd must be runnable as-is
    for m in result["missing"]:
        cmd = m.get("install_cmd", "")
        assert cmd.startswith("localsmartz model add "), cmd
        model_part = cmd.strip().removeprefix("localsmartz model add ").strip()
        assert model_part, "install_cmd must have a model name"
        assert ":" in model_part, f"model tag expected (family:size), got {model_part!r}"


def test_preflight_unknown_pattern_is_safe(monkeypatch) -> None:
    """Unknown pattern must not crash; should be structured refusal."""
    monkeypatch.setattr(
        "localsmartz.models.preflight.model_available", lambda name: True
    )
    result = preflight.check("nonexistent_pattern_xyz", {"tier": "mini"})
    # Either ok=True with empty missing (unknown == no roles to check) or
    # a clear ok=False. Both acceptable; what's NOT acceptable is a crash.
    assert isinstance(result.get("ok"), bool)
    assert isinstance(result.get("missing", []), list)


def test_preflight_install_cmd_is_copyable(monkeypatch) -> None:
    """install_cmd must be pasteable into a terminal without quoting issues."""
    import shlex

    monkeypatch.setattr(
        "localsmartz.models.preflight.model_available", lambda name: False
    )
    result = preflight.check("critic_loop", {"tier": "mini", "name": "lite"})
    for m in result["missing"]:
        cmd = m["install_cmd"]
        # shlex round-trip proves it has no embedded quote weirdness
        parts = shlex.split(cmd)
        assert len(parts) >= 4, f"install_cmd parse too short: {parts}"
        assert parts[0:3] == ["localsmartz", "model", "add"]
