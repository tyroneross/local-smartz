# Retrospective — local-smartz-2026-07-16-settings-enforcement-harness

_Date: 2026-07-17 · Repo: local-smartz_

_Prompts: 0 · Repeated clusters: 0 · Transcript present: False_

## Lessons learned

_no lessons captured (transcript empty or no prior signals)_

**Enrichment (no transcript reachable for this run — sourced from `reports/comparison-vs-agent-harness-2026-07-16.md`, `reports/fable-review-2026-07-16.md`, `.build-loop/plan.md`, and commits `2c5412b..60efd66`; narrated, not transcript-cited):**
- A written/declared setting is not the same fact as a consumed one — three independent instances this run: `active_model` was written by the UI schema and read by nothing (`plan.md:19-20`); the UI edited `agents/prompts/*.md` while every runtime call site read the embedded `system_focus` default instead (`fable-review-2026-07-16.md:29-34`, `agent.py:591,735,755`, `pipeline.py:136`); `--check`/doctor reported the hardcoded execution model tag "ready" against a config string, not against `GET /api/tags` reality (`comparison-vs-agent-harness-2026-07-16.md:26-27`). Same shape, three call sites — see meta-cause synthesis in §9.
- Fixing a loud failure can unmask a quieter, worse one. Leg A (broken model tag) 404'd in ~46s median — visible, diagnosable. Leg B (tag fixed) instead blew the 150s budget on 4/5 tasks with zero stdout in `--quiet` mode (`comparison-vs-agent-harness-2026-07-16.md:31-34`). The defect fix was correct; it was not sufficient without the heartbeat control from `fable-review-2026-07-16.md` rec #4.
- A pass/fail gate can hide a spec-to-runtime gap. `intent.md`'s north star ("agent roster, models, and privacy posture are user-controllable from Settings") was schema-true (fields existed, Swift wrote them, tests on the write path were green) but not runtime-true until this run's C1 wiring — the 830+ passing test suite gave no signal that `active_model` and prompt edits were inert (spec→current→desired gap, contested-meaning lens).

## Key takeaways

- Plan headline: Plan — Agent/model settings enhancements + prompt improvements (2026-07-16)
- Run outcome: pass
- ⚠️ "Run outcome: pass" above is pulled from `.build-loop/state.json`'s last `runs[]` entry, which is the stale 2026-05-08 LLM-provider-optimizations run — `state.json` was not updated for this 2026-07-16 run (only `plan.md`, mtime 17:53, was). Verified outcome for *this* run comes from commit messages instead: `60efd66` reports "1003 passed, 4 skipped; xcodebuild Debug BUILD SUCCEEDED" — pass, but the deterministic layer cannot see it because it reads a stale state file (see §9 recommended control).
- Posture: refine, not redirect/reset, for the overall settings/prompt architecture — `fable-review-2026-07-16.md:9-11` calls the plumbing "unusually complete" (per-agent models, cloud runners, secrets, patterns, 830-test suite); a from-scratch redesign is explicitly NOT warranted. One narrow exception: the coding route's false-success behavior is flagged "Highest-priority trust fix" (`fable-review-2026-07-16.md:47-52`) — a redirect call on that one boundary, not the app.
- Mid-flight interface freeze (`plan.md:35-77`, "Frozen backend contract") is what let C1 (backend), C2 (prompts), and C3 (Swift UI) dispatch as a genuine parallel batch (`plan.md:151-154,190-195`, "C1 ⊥ C2 ... C3 depends on C1's contract... dispatch in parallel") instead of serializing C3 behind C1's completion.

## Recommendations

_no recommendations this run_

- 10 ranked, not-yet-implemented recommendations already exist in `reports/fable-review-2026-07-16.md:46-86` (kill false success in the coding route; adopt oracle-gated execution; latency envelope + fast lane; progress heartbeat; always-on run log; settings-tab consolidation; remove the parallel `models/resolve.py` role→model system; split `serve.py`; embedded-vs-file prompt cleanup; unify secrets key naming). These carry no enforce-candidate file this run because the CLI's clustering runs off the transcript, which was unreachable (`transcript_present: false`) — human follow-up should manually route the top 2 (false-success gate, heartbeat) into `.build-loop/proposals/enforce-from-retro/` since they are load-bearing trust fixes, not style preferences.
- Single highest-leverage new control (see §9 meta-cause): a "declared-vs-consumed state" preflight — for every settings/schema key, assert at least one runtime read call site exists; for model tags, cross-check against `GET /api/tags` before reporting "ready." This would have caught the `active_model` orphan, the prompt-activation gap, and the `--check` false-ready defect in one script instead of three separate fixes discovered via a live 5-task harness comparison.

## What could be done better

_no failures captured this run_

- Comparison harness: local-smartz scored 0/5 oracle-pass on both legs vs. the harness's 3/5 (`comparison-vs-agent-harness-2026-07-16.md:15-21`) — not a model-quality gap (same models available to both), a missing-gate gap: no oracle verification before a success claim (`fable-review-2026-07-16.md:56-57`).
- Counterfactual: a single declared-vs-consumed preflight (see Recommendations) would have removed the need for the 3-way explorer pass (model flow / app UI / cloud integration) AND the live 5-task harness comparison to *discover* the active_model-orphan and prompt-activation-gap defects — both were static, greppable mismatches, not runtime-dependent behavior. What that preflight would NOT remove: the human call on rec #1 (kill false success in the coding route) — whether "advisory-only, rc=0" is ever acceptable UX is a product tradeoff, not a defect a script should auto-fix.
- 5 security findings (2 HIGH: LangSmith trace egress, CORS on state-changing settings routes) surfaced at the Review-A security-reviewer pass, after C1/C2/C3 already landed (`60efd66` commit body) — the plan's own risk section only anticipated 3 of the 4 threat vectors it later needed (`plan.md:163-171` T1-T4 vs. the LangSmith egress path, which isn't named there). The review gate caught it before push, but the threat-model draft under-scoped LLM-observability egress as a local_only vector at plan time.

## What went well

_no positive judge verdicts captured this run_

- Full pytest suite green (1003 passed, 4 skipped) and `xcodebuild Debug BUILD SUCCEEDED` after the review-pass iterate, plus a live serve smoke (evil-Origin 403, localhost/no-Origin 200, settings round-trip) — all re-run by the orchestrator, not taken on implementer claim (`60efd66` commit body, `fable-review-2026-07-16.md:100-107`).
- Independent-auditor pass (4 findings) and security-reviewer pass (5 findings, 2 HIGH) both ran at Review-A per the plan's own gate (`plan.md:172` "security-reviewer runs at Review-A") and all 9 were fixed in the same iterate cycle before push — the gate worked as designed.
- The frozen backend contract (`plan.md:35-77`) held across the parallel batch: C3 (Swift UI) coded against `GET/POST /api/settings`, `GET /api/models`, and the two FROZEN JSON shapes without needing to read C1-owned Python files, and the post-freeze addendum flow (contract clarification sent to a running implementer) landed cleanly — reported by the dispatching orchestrator as working twice this run; ⚠️ unverified independently (no transcript reachable to confirm the SendMessage exchanges directly).

## What went well by accident

_(no signals captured — populate from agent reflection)_

**Planned and earned:**
- The frozen-contract discipline (Path B choices: one `/api/settings` endpoint, `LocalOnlyError` at the runner-factory boundary rather than UI-only hiding — `plan.md:143-149`) was a deliberate design choice, not luck, and it is what made the C1⊥C3 parallel dispatch and the clean addendum flow possible.
- The comparison harness (5-task, deterministic oracles + independent Opus judge) was scoped and run deliberately to surface local-smartz's own defects before shipping the settings feature — it earned the 3 defect findings it got.

**Lucky / unplanned good:**
- The broken execution-model tag (leg A) happened to fail *loud* (404 in ~46s) rather than silently — that accident of failure-mode timing is what made the defect cheap to catch via the harness at all. Had the tag instead resolved to some other installed-but-wrong model, the defect could have shipped invisibly. The subsequent quiet-timeout failure mode (leg B) shows this was not a designed property of the system, just where this particular defect happened to land.

## What should be enforced

_no enforce-candidates this run_

- Declared-vs-consumed state preflight (single enforce-candidate family, see §9 meta-cause): for every settings/schema key added to `global_config.py` or `.localsmartz/config.json`, require a paired runtime-consumer reference (grep-verifiable); for any model tag surfaced by `--check`/doctor, require the check to query `GET /api/tags` rather than assert against the config string.
- Mandatory artifact-verification gate before a coding/mutation task reports success (rc=0) — `fable-review-2026-07-16.md:47-52` rec #1, ranked "Highest-priority trust fix."
- `--quiet` heartbeat: emit a stderr status line (stage + elapsed + model) every ~10s even in quiet mode — `fable-review-2026-07-16.md:63-65` rec #4; directly motivated by the leg-B silent-timeout finding.
- `.build-loop/state.json`'s `runs[]`/`execution` block should be refreshed at run closeout even for manually-orchestrated runs — this run's retrospective otherwise surfaces a stale outcome (see §2 note) purely because state.json lagged plan.md by 2+ months.

## User prompts this thread (with repeats)

_no transcript for this run (host=unknown, window=2026-05-08)_

## Issues (with causal tree)

_No issues surfaced this run._

**Enrichment — issues reconstructed from `reports/` + `plan.md` + commit bodies (no transcript to cite lines from):**

1. **`active_model` orphan setting** (`plan.md:19-20`) — schema + Swift wrote it, no Python reader applied it, until this run.
   - Why: no runtime code path consumed the key.
   - Why: nothing asserted that a written settings key has ≥1 reader.
   - Missing system control: a settings-schema contract test that fails CI when a key exists in the schema with zero grep-matched consumer.

2. **Prompt activation gap** (`fable-review-2026-07-16.md:29-34`) — UI edited/displayed `agents/prompts/*.md`; runtime read the embedded `system_focus` default at `agent.py:591,735,755` and `pipeline.py:136` instead.
   - Why: two parallel representations of the same prompt (file vs. embedded string) with no single loader.
   - Why: the embedded default predates the file-based prompt system and was never removed once `.md` files became UI-editable.
   - Missing system control: one canonical `get_role_prompt()` loader was the fix (now shipped); the durable control is a lint/test that fails if any call site references `system_focus` directly instead of the loader.

3. **`--check`/doctor reported an uninstalled model tag "ready"** (`comparison-vs-agent-harness-2026-07-16.md:26-27`, `fable-review-2026-07-16.md:24-28`) — full profile hardcoded `qwen2.5-coder:32b-instruct-q5_K_M`, not present in `ollama list`; runtime 404'd.
   - Why: `--check` validated presence of a config string, not presence in `GET /api/tags`.
   - Why: only the planning-model path had an availability-fallback; execution/agent-model resolution didn't.
   - Missing system control: exact-tag validation against live Ollama tags, applied uniformly to every model-resolution path (now shipped for this run's paths — needs a regression test that a *new* role/path can't skip it).

**Meta-cause synthesis (issues 1-3 share one root):** all three are the same failure shape — a value is *declared* (in schema, in a config file, in a UI edit) and treated as true without checking whether it is *actually consumed* (by runtime code) or *actually true* (installed model reality). File ONE preflight family, not three enforce-candidates: a "declared-vs-consumed state verifier" — (a) schema/settings keys require a grep-verified runtime reader, (b) model-tag claims require a live availability check, not a config-string check. This is the same shape as the build-loop-memory decision-0095 worked example (placeholder secret, gitignored CI config, subagent miscount → "trusted asserted state over actual state").

4. **Coding route false-success signal** (`comparison-vs-agent-harness-2026-07-16.md:28-29`, `fable-review-2026-07-16.md:19-20,46-52`) — t2 exited 0 in ~10s with advisory prose ("Requires human review") while pytest still failed, both harness legs.
   - Why: the guarded coding loop (`coding_loop.py`/`coding_harness.py`) returns rc=0 for an advisory/analysis-only outcome, indistinguishable from a real fix.
   - Why: no oracle gate (test run / doctest / parse) sits between "agent produced text" and "process exit code."
   - Missing system control: mandatory verification-before-success-claim gate (rec #1/#2, `fable-review-2026-07-16.md:46-57`) — not fixed this run (out of this run's chunk scope), carried forward as the top open recommendation.

5. **Silent 150s timeout after the model-tag fix** (`comparison-vs-agent-harness-2026-07-16.md:31,33-34`) — leg B: 4/5 tasks blew the budget with zero stdout in `--quiet` mode; hung vs. working was indistinguishable.
   - Why: `--quiet` suppresses all progress signal, including the SSE-equivalent heartbeat the app itself already emits.
   - Why: no minimum heartbeat cadence is enforced independent of verbosity level.
   - Missing system control: a verbosity floor — `--quiet` reduces detail, never to zero liveness signal (rec #4, `fable-review-2026-07-16.md:63-65`).

## Plugin & tooling observations

_no transcript for this run (host=unknown, window=2026-05-08)_

**Enrichment — no per-call-site counts possible without a transcript; tool/skill usage below is reconstructed from `plan.md` + commit diffs, so treat as shape evidence, not deterministic counts:**
- **prompt-builder skill** (`Skill("prompt-builder:prompt-builder")`, `plan.md:119`) — used by C2 to rewrite all 9 agent prompts for small/mid local-model calibration; diff evidence (`034034d` stat) shows 8 prompt files touched, 300+ combined insertions/deletions, substantive not cosmetic. Enhancement: the skill's output contract could add a mandatory "consumer-check" line (grep the codebase for other readers of the prompt's role name / any embedded-default fallback) as part of its own checklist — this would have surfaced the prompt-activation-gap (issue #2 above) at rewrite time instead of at the harness-comparison/review stage.
- **independent-auditor + security-reviewer subagents** — both ran at Review-A per the plan's own gate (`plan.md:172`) and both surfaced real findings (4 + 5, commit `60efd66` body). Enhancement: they fire once, post-landing, across the whole diff; a lighter per-chunk security lint (e.g., "any new HTTP route mutating state → check Origin handling" as a grep-based pre-commit check) would catch the CORS-shaped class of finding before Review-A rather than at it, cutting the review→iterate round-trip.
- **xcodebuild** (Debug build check, `plan.md:140-141`) — confirmed `BUILD SUCCEEDED` in `60efd66`. Enhancement: build-green was the only App-side verification cited; no live UI/visual check is mentioned for the new Settings/Agents/Pattern/Eval tab changes (200+ line SettingsView.swift diff) — worth a follow-up IBR or screenshot pass per this project's own UI-audit practice.
- **Comparison-harness tooling** (scratchpad `compare/runner.py`, `verify.py`, judge invocation — `comparison-vs-agent-harness-2026-07-16.md:9`) — deterministic oracles + independent Opus judge audit, two legs, both scored and cited. These lived in scratchpad only; if this comparison format gets reused (it already ran once before this project, per the report's own framing), promote `runner.py`/`verify.py` out of scratchpad into a committed `scripts/harness/` location (see §11).

## Deterministic-automation candidates

_No recurring automatable sequence detected this thread._

**Enrichment — candidates reconstructed from the reports/plan (no transcript to confirm exact repetition counts):**
- **Declared-vs-consumed state preflight** (directly from §9 meta-cause) — a script that, given a settings schema module and a source tree, flags any schema key with zero grep-matched runtime readers, and any model-tag string not cross-checked against `GET /api/tags`. Concrete home: `scripts/hooks/declared_vs_consumed_preflight.py`, wired to run before Phase 2 Plan synthesis (so it informs planning, not just post-hoc review) and again at Phase 4 Review.
- **Comparison-harness runner** — `runner.py` + `verify.py` (oracle scoring) + judge-report generation currently live only in ephemeral scratchpad (`comparison-vs-agent-harness-2026-07-16.md:9`). This is the second time this shape of comparison has been run for this project's evaluation needs (cf. `.build-loop/evals/2026-05-19-agent-scorecard.md`); promoting it to a committed `scripts/harness/compare_agents.py` (+ fixture task set) would remove the "re-author the runner from scratch in scratchpad" step each time and let the oracle thresholds be versioned.
- **Review-A dual-subagent dispatch** (independent-auditor + security-reviewer, run together at the same checkpoint per `plan.md:172`) already reads as a fixed ritual, not ad hoc — worth codifying as an explicit `scripts/hooks/review_a_gate.py` that dispatches both by name whenever a chunk touches HTTP routes or auth/privacy-boundary code, rather than relying on the plan author to remember to schedule it.
