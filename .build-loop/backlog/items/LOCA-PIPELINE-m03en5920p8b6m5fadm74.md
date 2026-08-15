---
id: LOCA-PIPELINE-m03en5920p8b6m5fadm74
schema_version: 1
title: Expose plugin/MCP tools + skills on the default LangGraph pipeline?
status: open
priority: P2
type: decision
area: pipeline
bucket: decision
workstream: pipeline
related_to: []
decision_options: ["A: keep off (tighter surface for small local models; documented 2026-08-15)", "B: grant researcher role a dynamic-tool policy (prefix allow: plugin_*, mcp_*) + tie MCP client lifetime to _build_agents_for_roles cache", "C: expose only to a new top-level router node, mirroring legacy orchestrator-only behavior"]
decision_impacts: [Plugins/MCP installed via CLI are inert on the default path today; enabling changes tool budget for qwen3:8b-class models and needs close_mcp_clients on cache eviction]
entities: []
gated: product-decision
provenance:
  source: oc-task
  ref: 0a4b19148807bea22faedc67f2d7b8e3
evidence: [src/localsmartz/pipeline.py §Known limitation; tests/test_pipeline.py::test_build_tool_registry_excludes_plugin_and_mcp; ~/dev/docs/audits/2026-08-15-data-intel-repo-audit/local-smartz.md D7]
supersedes: null
superseded_by: null
created: 2026-08-15
updated: 2026-08-15
review_by: 2026-09-14
owner: unassigned
---

## Context
<why this matters / what's the situation>

## Acceptance
- <verifiable condition 1>

## Notes
<additional detail>
