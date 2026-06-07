# Agent Scorecard Approaches for Local Smartz

## Bottom Line

Build the scorecard as a deterministic harness first, then add optional live
LLM judging later. For local-smartz, the highest-value scorecard is not another
answer-quality benchmark. It is a system-readiness check across routing,
specialist-role selection, pattern availability, local tier gates, and
traceability.

## Sources Used

- LLM wiki: `wiki/sources/source-agentic-architectures-fieldguide-2026.md`
- LLM wiki: `raw/research/agent-state-architecture-reference-2026-05.md`
- Research inbox: `2026-04-23-stratagem-local-first-multi-agent-architecture.md`
- LangChain AgentEvals docs: deterministic trajectory match vs LLM-as-judge
- OpenAI Agents SDK docs: tracing and guardrails
- OpenTelemetry GenAI agent span conventions
- Anthropic: Building effective agents
- Anthropic: How we built our multi-agent research system

## Decision

V1 scorecard dimensions:

1. Routing/runtime fit: trivial prompts should stay fast; research/debug/data
   prompts should route to the graph or agent path.
2. Specialist-role fit: expected roles should be selected with no missing
   critical specialist and no unnecessary extras.
3. Pattern readiness: recommended local-friendly patterns must exist in the
   registry: single, chain, router, critic loop, reflection.
4. Local safety gates: orchestrator-workers must stay tier-gated; debate stays
   deferred until strong local or cloud models are explicit.
5. Auditability: tracing and PII-redacted span export must be present before
   deeper multi-agent work.
6. Outcome baseline: keep the existing golden answer harness separate so model
   answer regressions do not get mixed with agent-trajectory regressions.

## Why This Improves The App

The scorecard makes the agent system measurable before a user feels the
regression. It catches cases where:

- a current-data prompt accidentally falls back to a fast answer path;
- a debugging prompt stops selecting planner or analyzer roles;
- a pattern exists in code but is unsafe for the detected local hardware tier;
- agent traces are not sufficient to answer why a run failed.

That turns the agent-system work from "more complex architecture" into a
visible quality gate in the Eval tab and a CLI gate for future Build Loop runs.

## Deferred

- LLM-as-judge quality grading for free-form research outputs.
- Trace replay scoring from actual OTel spans.
- Pattern-specific live trajectory checks for chain, critic loop, reflection,
  and orchestrator runs.
