# DeepAgents open-source alternatives for Local Smartz

- **Project**: `/Users/tyroneross/dev/git-folder/local-smartz`
- **Date**: 2026-05-19
- **Mode**: build-loop research, balanced
- **Question**: Are there better open-source alternatives to DeepAgents for Local Smartz, and what is the best path?

---

## Bottom line

Do **not** replace DeepAgents for licensing reasons alone. DeepAgents is MIT-licensed, and the current repo already uses LangGraph/ReAct as the default deterministic runtime. The best path is to **promote the existing LangGraph pipeline and AgentRunner patterns to the primary open-source architecture, keep DeepAgents as an optional compatibility harness, and remove the hard dependency only after a parity eval proves no regression**.

## Repo findings

- `pyproject.toml` directly depends on `deepagents`.
- `src/localsmartz/agent.py` is the legacy/full-agent DeepAgents path: `create_deep_agent`, filesystem backend, checkpointer, built-in task delegation.
- `src/localsmartz/pipeline.py` is already a deterministic LangGraph pipeline. It runs researcher/analyzer/fact_checker/writer via `langchain.agents.create_agent`, uses `Send` fanout, and encodes the fact-check loop in graph edges.
- `src/localsmartz/routing.py` already routes normal non-fast prompts to `graph_pipeline` when enabled. Focus mode or explicit opt-out falls back to `full_agent`.
- `src/localsmartz/patterns/orchestrator.py` has two worker paths:
  - Path A: DeepAgents subagents and `task`.
  - Path B: pure `AgentRunner.run_turn` worker dispatch with no DeepAgents machinery.
- `src/localsmartz/agent_role_doe.py` already provides a deterministic role-selection eval surface. This should be extended before removing any runtime.

## External findings

- DeepAgents is open source and MIT-licensed. It is an opinionated harness on top of LangGraph with planning, filesystem, context management, subagents, tools, and skills. Source: https://github.com/langchain-ai/deepagents
- LangGraph is MIT-licensed and is the lower-level runtime for long-running, stateful agents. Source: https://github.com/langchain-ai/langgraph
- Anthropic's agent guidance says to start with simple composable patterns and add agentic complexity only when it demonstrably improves outcomes. Source: https://www.anthropic.com/engineering/building-effective-agents
- Anthropic's multi-agent research writeup supports orchestrator-worker systems for breadth-first research, but notes high cost and coordination complexity. Source: https://www.anthropic.com/engineering/multi-agent-research-system
- Pydantic AI is MIT-licensed, model-agnostic, and has OTel-compatible observability, but adopting it would replace large parts of the current LangChain/LangGraph tool and agent stack. Source: https://github.com/pydantic/pydantic-ai
- Hugging Face smolagents is Apache-2.0 and local/Ollama-capable, but it is code-agent oriented and would require a larger rewrite of tool execution and safety boundaries. Source: https://github.com/huggingface/smolagents
- OpenAI Agents SDK is MIT-licensed and provider-agnostic, with handoffs, guardrails, sessions, tracing, and sandbox agents. It is viable for new work, but it would be a framework migration rather than a small improvement to this repo. Source: https://github.com/openai/openai-agents-python
- Microsoft AutoGen is in maintenance mode; Microsoft Agent Framework is its successor. AutoGen should not be chosen for new Local Smartz architecture. Sources: https://github.com/microsoft/autogen and https://github.com/microsoft/agent-framework
- CrewAI is MIT-licensed and popular for role-playing multi-agent orchestration, but it is a higher-level framework that would duplicate current Local Smartz roles/patterns rather than simplify them. Source: https://github.com/crewAIInc/crewAI
- LlamaIndex is MIT-licensed and strong for document/RAG agents, but Local Smartz's core problem is local multi-agent research orchestration, not primarily indexing. Source: https://github.com/run-llama/llama_index

## Option ranking

1. **Best path: LangGraph + repo-local AgentRunner, keep DeepAgents optional**
   - Lowest migration cost.
   - Preserves existing tests, routing, providers, tools, SSE, Phoenix tracing, role prompts, and model evals.
   - Keeps the project open source without introducing a new framework.
   - Converts DeepAgents from product identity to optional harness.

2. **Second best: Pydantic AI**
   - Good open-source posture and typed developer experience.
   - Better if Local Smartz wants to reduce LangChain coupling over time.
   - Not the best immediate move because it rewrites existing tool and graph integration.

3. **Third: OpenAI Agents SDK**
   - Strong harness primitives and MIT license.
   - Good for a new agent product or a sidecar prototype.
   - Not ideal as the core Local Smartz runtime because the repo is already LangGraph-shaped and local-first via Ollama.

4. **Niche: smolagents**
   - Good for lightweight code agents and local/Ollama demos.
   - Poor fit as the main Local Smartz research/runtime framework because code execution becomes the center of gravity.

5. **Avoid for core migration: AutoGen, CrewAI, LlamaIndex**
   - AutoGen: maintenance mode.
   - CrewAI: too much high-level orchestration overlap.
   - LlamaIndex: better for RAG/document workflows than this runtime problem.

## Recommended implementation path

1. Rebrand runtime docs from "powered by DeepAgents" to "powered by Ollama + LangGraph, with optional DeepAgents harness".
2. Make `deepagents` an optional dependency group, not a base dependency.
3. Ensure `graph_pipeline` and `patterns/orchestrator.py` Path B cover the current DeepAgents use cases:
   - planning/todos
   - filesystem artifacts
   - subagent/context isolation
   - checkpointing/thread continuity
   - skill/plugin prompt injection
4. Extend evals before code removal:
   - add `localsmartz-runtime-parity-eval`
   - compare `graph_pipeline` vs DeepAgents `full_agent`
   - score final answer, roles selected, tool calls, artifact creation, latency, and failure mode
5. Remove the hard dependency only if parity passes on local models and one cloud fallback.

## Risk

The risk is not license risk. It is **behavioral parity risk**. DeepAgents bundles planning, filesystem, subagents, context management, and skills. Removing it without an eval harness could quietly degrade long-horizon behavior.

## Confidence

- Context coverage: high
- Verification coverage: medium
- Evidence quality: high for license/runtime facts; medium for migration effort
- Overall: high

## Next action

Implement the runtime parity eval first. Do not swap frameworks until the eval shows where DeepAgents still provides value.
