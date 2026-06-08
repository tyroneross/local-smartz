"""Build-loop-inspired coding task planner for Local Smartz.

This module ports the parts of Build Loop that make sense inside an offline
local app: phase discipline, model-tier policy, risk classification, and
verification planning. It intentionally does not copy host-specific machinery
such as subagent fan-out, Rally coordination, auto-commit, deployment gates, or
memory backends.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import time

from langchain_ollama import ChatOllama

from localsmartz.coding_harness import build_coding_context, resolve_coding_model


PHASES: tuple[str, ...] = (
    "assess",
    "plan",
    "guardrails",
    "execute_plan",
    "verify",
    "review",
    "iterate",
    "report",
)

_WRITE_TERMS = (
    "implement",
    "fix",
    "refactor",
    "edit",
    "patch",
    "add",
    "update",
    "change",
    "wire",
    "build",
    "create",
)

_BLOCKED_TERMS = (
    "force push",
    "push origin",
    "production",
    "prod deploy",
    "deploy to prod",
    "delete production",
    "delete secrets",
    "remove secrets",
    "credential",
    "api key",
    "access token",
    "bearer token",
    "oauth token",
    "secret token",
    "rm -rf",
    "reset --hard",
    "drop database",
    "sudo ",
    "chmod ",
    "chown ",
)

_SMALL_MODEL_RE = re.compile(r"(^|[:_-])(3b|7b|8b|9b)([:_-]|$)", re.IGNORECASE)
_LARGE_MODEL_RE = re.compile(r"(^|[:_-])(70b|120b)([:_-]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class ModelPolicy:
    model: str
    tier: str
    max_context_tokens: int
    can_propose_patch: bool
    can_apply_edits: bool
    requires_human_review: bool
    allowed_outputs: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class GuardrailDecision:
    status: str
    risk: str
    reasons: tuple[str, ...]
    model_policy: ModelPolicy

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


def model_policy_for(model_name: str | None) -> ModelPolicy:
    """Return Local Smartz's coding-loop policy for a selected model."""
    name = (model_name or "").strip()
    lowered = name.lower()

    if "qwen2.5-coder" in lowered or "codestral" in lowered or "deepseek-coder" in lowered:
        return ModelPolicy(
            model=name,
            tier="code-local",
            max_context_tokens=8192,
            can_propose_patch=True,
            can_apply_edits=False,
            requires_human_review=True,
            allowed_outputs=("assessment", "plan", "patch_strategy", "verify_commands"),
            notes=(
                "Good fit for bounded implementation planning.",
                "May propose patches, but Local Smartz v1 does not apply them.",
            ),
        )

    if "gpt-oss:20b" in lowered or _LARGE_MODEL_RE.search(lowered):
        return ModelPolicy(
            model=name,
            tier="synthesis-local",
            max_context_tokens=8192,
            can_propose_patch=True,
            can_apply_edits=False,
            requires_human_review=True,
            allowed_outputs=("assessment", "plan", "tradeoffs", "verify_commands"),
            notes=(
                "Useful for cross-file planning and ambiguity resolution.",
                "Use a coder model for exact patch drafting when available.",
            ),
        )

    if _SMALL_MODEL_RE.search(lowered):
        return ModelPolicy(
            model=name,
            tier="pattern-local",
            max_context_tokens=4096,
            can_propose_patch=False,
            can_apply_edits=False,
            requires_human_review=True,
            allowed_outputs=("assessment", "plan", "questions", "verify_commands"),
            notes=(
                "Plan-only guardrail for smaller local models.",
                "Do not ask this model to produce multi-file patches.",
            ),
        )

    return ModelPolicy(
        model=name,
        tier="standard-local",
        max_context_tokens=6144,
        can_propose_patch=False,
        can_apply_edits=False,
        requires_human_review=True,
        allowed_outputs=("assessment", "plan", "verify_commands"),
        notes=(
            "Unknown coding capability; use conservative plan-only behavior.",
        ),
    )


def _contains_any(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(term for term in terms if term in text)


def classify_coding_loop_request(prompt: str, model_name: str | None) -> GuardrailDecision:
    """Classify the coding request before involving the model."""
    policy = model_policy_for(model_name)
    lowered = (prompt or "").lower()
    blocked_hits = _contains_any(lowered, _BLOCKED_TERMS)
    if blocked_hits:
        return GuardrailDecision(
            status="blocked",
            risk="high",
            reasons=tuple(f"blocked term: {term}" for term in blocked_hits),
            model_policy=policy,
        )

    write_hits = _contains_any(lowered, _WRITE_TERMS)
    if write_hits and policy.can_propose_patch:
        return GuardrailDecision(
            status="review_required",
            risk="medium",
            reasons=(
                "write-like task detected",
                "model may propose a patch strategy but cannot apply edits",
            ),
            model_policy=policy,
        )

    if write_hits:
        return GuardrailDecision(
            status="review_required",
            risk="medium",
            reasons=(
                "write-like task detected",
                "selected model is restricted to plan-only output",
            ),
            model_policy=policy,
        )

    return GuardrailDecision(
        status="allowed",
        risk="low",
        reasons=("read-only coding assessment",),
        model_policy=policy,
    )


def _guardrail_text(decision: GuardrailDecision) -> str:
    payload = asdict(decision)
    return "\n".join(
        [
            f"status: {payload['status']}",
            f"risk: {payload['risk']}",
            "reasons:",
            *[f"- {reason}" for reason in decision.reasons],
            "model_policy:",
            f"- model: {decision.model_policy.model}",
            f"- tier: {decision.model_policy.tier}",
            f"- max_context_tokens: {decision.model_policy.max_context_tokens}",
            f"- can_propose_patch: {decision.model_policy.can_propose_patch}",
            f"- can_apply_edits: {decision.model_policy.can_apply_edits}",
            f"- requires_human_review: {decision.model_policy.requires_human_review}",
            "allowed_outputs:",
            *[f"- {item}" for item in decision.model_policy.allowed_outputs],
            "notes:",
            *[f"- {item}" for item in decision.model_policy.notes],
        ]
    )


def build_coding_loop_prompt(
    prompt: str,
    *,
    cwd: Path,
    model_name: str,
) -> tuple[str, GuardrailDecision]:
    """Build the full prompt packet and guardrail decision."""
    decision = classify_coding_loop_request(prompt, model_name)
    context = build_coding_context(prompt, cwd)
    phase_lines = ", ".join(PHASES)
    user = f"""\
## User Task
{prompt}

## Coding Loop Phases To Use Internally
{phase_lines}

## Guardrail Decision
{_guardrail_text(decision)}

## Workspace Context
{context}

## Required Answer Shape
Start with one direct recommendation sentence. Then use only these sections:
1. Current Read
2. Exact Files
3. Minimal Plan
4. Verification

Rules:
- Do not claim to have edited files.
- Do not say work was completed, implemented, refactored, added, or verified.
- If a file exists in the workspace context, say it exists instead of calling it missing.
- Prefer the smallest file-local plan over broad architecture rewrites unless the task asks for architecture migration.
- Do not output shell commands that push, deploy, delete secrets, or mutate production.
- If status is blocked, explain the block and give a safe alternative.
- For small/pattern-local models, keep the plan narrow and ask for human review before patching.
- For code-local models, propose the smallest patch strategy and validation commands.
"""
    return user, decision


_CODING_LOOP_SYSTEM_PROMPT = """\
You are Local Smartz in coding-loop mode.

Follow a compact Build Loop-inspired process internally. You are operating in read-only mode.
You may propose patch strategy and verification commands, but you must not
claim that files were changed or that execution is complete. Respect the
guardrail decision exactly.
"""


def coding_loop_stream(
    prompt: str,
    profile: dict,
    *,
    cwd: Path,
    model_override: str | None = None,
):
    """Yield SSE-style events for a guarded coding-loop response."""
    import httpx

    start = time.time()
    model_name = resolve_coding_model(profile, model_override)
    user, decision = build_coding_loop_prompt(prompt, cwd=cwd, model_name=model_name)

    yield {
        "type": "text",
        "content": (
            f"[coding-loop] using {model_name} "
            f"({decision.model_policy.tier}, {decision.status})\n\n"
        ),
    }
    for phase in PHASES[:3]:
        yield {"type": "stage", "stage": phase}

    if decision.blocked:
        yield {
            "type": "text",
            "content": (
                "This coding task is blocked by Local Smartz guardrails.\n\n"
                f"{_guardrail_text(decision)}\n\n"
                "Safe alternative: ask for a read-only assessment, a patch plan, "
                "or local verification commands that do not push, deploy, or "
                "touch secrets."
            ),
        }
        yield {
            "type": "done",
            "duration_ms": int((time.time() - start) * 1000),
            "thread_id": "",
        }
        return

    llm = ChatOllama(
        model=model_name,
        temperature=0,
        num_ctx=decision.model_policy.max_context_tokens,
        keep_alive="30m",
        client_kwargs={
            "timeout": httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0),
        },
    ).with_retry(
        stop_after_attempt=2,
        wait_exponential_jitter=True,
        retry_if_exception_type=(httpx.TransportError, httpx.TimeoutException),
    )

    try:
        for chunk in llm.stream(
            [
                {"role": "system", "content": _CODING_LOOP_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ]
        ):
            content = getattr(chunk, "content", None)
            if isinstance(content, str) and content:
                yield {"type": "text", "content": content}
            elif isinstance(content, list):
                for seg in content:
                    text = seg.get("text") if isinstance(seg, dict) else None
                    if isinstance(text, str) and text:
                        yield {"type": "text", "content": text}
    except Exception as exc:  # noqa: BLE001
        yield {
            "type": "tool_error",
            "name": "coding_loop",
            "message": f"Coding-loop LLM call failed: {exc}",
        }

    yield {
        "type": "done",
        "duration_ms": int((time.time() - start) * 1000),
        "thread_id": "",
    }


__all__ = [
    "GuardrailDecision",
    "ModelPolicy",
    "PHASES",
    "build_coding_loop_prompt",
    "classify_coding_loop_request",
    "coding_loop_stream",
    "model_policy_for",
]
