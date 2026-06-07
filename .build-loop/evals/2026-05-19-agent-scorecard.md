# Agent System Scorecard

- **Score:** 100.000
- **Grade:** pass
- **Hardware tier:** mini
- **Task contract:** 10 pass / 0 fail
- **System checks:** 6 pass / 0 fail

## Task Contract

| case | score | runtime | roles | pattern | dimensions |
|---|---:|---|---|---|---|
| fast_math | 100.0 | fast_path (ok) | none | single (ok) | routing, latency |
| current_stock | 100.0 | graph_pipeline (ok) | researcher,fact_checker,writer | chain (ok) | freshness, source_quality, trajectory |
| compare_languages | 100.0 | graph_pipeline (ok) | researcher,analyzer,fact_checker,writer | critic_loop (ok) | reasoning, critique, decision_quality |
| summarize_doc | 100.0 | graph_pipeline (ok) | researcher,writer | chain (ok) | context, compression, artifact_hygiene |
| csv_chart | 100.0 | graph_pipeline (ok) | analyzer,writer | chain (ok) | tool_use, analysis, artifact_hygiene |
| citations | 100.0 | graph_pipeline (ok) | researcher,fact_checker,writer | chain (ok) | source_quality, guardrails, traceability |
| model_choice | 100.0 | graph_pipeline (ok) | researcher,writer | critic_loop (ok) | decision_quality, local_safety, critique |
| debug_launch | 100.0 | graph_pipeline (ok) | planner,researcher,analyzer,writer | reflection (ok) | state, traceability, recovery |
| write_report | 100.0 | graph_pipeline (ok) | researcher,fact_checker,writer | critic_loop (ok) | structure, critique, quality |
| analyze_earnings | 100.0 | graph_pipeline (ok) | researcher,analyzer,fact_checker,writer | chain (ok) | analysis, freshness, source_quality |

## System Checks

| check | category | score | evidence |
|---|---|---:|---|
| pattern_registry_coverage | trajectory | 100.0 | available=['chain', 'critic_loop', 'orchestrator', 'parallel', 'reflection', 'router', 'single'] |
| deterministic_role_contract | trajectory | 100.0 | weighted_accuracy=1.0 |
| runner_abstraction | open_source_harness | 100.0 | patterns consume localsmartz.runners.AgentRunner instead of provider SDKs directly |
| audit_trace_hooks | observability | 100.0 | get_tracer and PII redaction are available for OTel-compatible spans |
| local_safety_gates | local_model_safety | 100.0 | debate_present=False; orchestrator_description=Lead decomposes into workers; workers write artifacts; lead synthesizes from summaries. One-level-only (P1). Requires standard+ tier by default. |
| golden_answer_harness | outcome_quality | 100.0 | golden_tasks=10 |

## Research Basis

- **LLM wiki agent-state architecture:** Score traceability, cost, quality, tool patterns, latency, and guardrail decisions.
- **LLM wiki local-first multi-agent architecture:** Prefer single, chain, routing, and evaluator-optimizer locally; gate orchestrator-workers by tier.
- **LangChain AgentEvals:** Use deterministic trajectory checks when expected tool/role paths are known; reserve LLM judges for open quality.
- **OpenAI Agents SDK tracing and guardrails:** Agent runs should expose spans for generations, tools, handoffs, guardrails, and custom events.
- **Anthropic effective agents and multi-agent research:** Start simple, evaluate early on small samples, and scale agent effort only when task value justifies cost.
