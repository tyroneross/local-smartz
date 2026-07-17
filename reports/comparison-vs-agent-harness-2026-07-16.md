# 5-task comparison: local-smartz vs rosslabs-agent-harness — 2026-07-16

Method: 5 moderate tasks (code-write, bug-fix, JSON extraction, summarization,
CSV math), identical prompts, isolated workspaces, 150s timeout, deterministic
oracles + independent Opus judge audit. Two legs: A = as-configured, B = after
fixing smartz's broken execution-model tag (Ollama alias). Harness routed itself
to qwen3-coder:30b; smartz auto-picked qwen3:8b planning + 32B execution (full
profile). Repos @ edf379f (smartz) / b5824fb (harness).
Artifacts: scratchpad `compare/` (runner.py, verify.py, runs*, judge-report.md).

## Scoreboard

| | smartz (A) | smartz (B) | harness |
|---|---|---|---|
| Oracle pass | 0/5 | 0/5 | **3/5** (t2 bug-fix, t4 summary, t5 CSV) |
| Median wall-clock | 45.8s (mostly errors) | 150s (timeouts) | **38s** |
| Task artifacts created | 0 | 0 | 5/5 runs |
| Judge: result integrity (0-5) | — | 2.0 | 3.4 |
| Judge: output quality | — | 0.4 | 4.0 |
| Judge: logging/observability | — | 0.4 | 4.2 |
| Judge: failure behavior | — | 1.2 | 3.6 |

## What the audit established

**local-smartz** (0/5 both legs — failure modes, not model quality):
1. Broken config shipped: full profile hardcodes an uninstalled model tag;
   `--check` calls it "ready"; runtime 404s (leg A t1/t3/t5).
2. Coding route substitutes planning for execution: t2 exited 0 in ~10s having
   modified nothing ("Requires human review") — a false success signal, both legs.
3. With valid config (leg B), the multi-agent pipeline blew the 150s budget on 4/5
   tasks with **zero stdout** in --quiet mode — silent, unobservable timeouts.
4. No run left any log or artifact beyond `.localsmartz/config.json`; `-o out/`
   never materialized. Paradox: the loud, fast 404 of leg A was better failure
   behavior than the silence of leg B.

**rosslabs-agent-harness** (3/5 — capable, overconfident):
1. Executes real tool calls, creates artifacts every run, event-sourced JSONL
   session logs made every defect traceable in minutes.
2. Declares success without verifying: t1 shipped doctests it never ran (2/5
   fail, including the example it printed as proof); t3 wrote invalid JSON
   (backtick escapes + dropped quote) while the transcript rendered clean JSON —
   display ≠ disk.
3. Verification is inconsistent: it ran pytest for t2 (pass) but skipped
   doctest/JSON-parse for t1/t3. A mandatory parse/run-the-artifact gate before
   the success claim plausibly lifts it to 5/5 integrity.

## Actions taken from these findings (build-loop run, same day)
- smartz defect fixes: execution-model tag + availability fallback, exact-tag
  `--check`, prompt activation gap (see reports/fable-review-2026-07-16.md).
- Remaining recommendations (false-success coding route, oracle-gated execution,
  fast-lane routing, heartbeat, run logs) ranked in the same review doc.
- Harness recommendation (its repo, not changed this run): make artifact
  verification (doctest/pytest/JSON-parse) unconditional before success claims —
  its `--verify` flag exists but is opt-in.

Status: ✅ verified by deterministic oracles (verify.py) + independent Opus judge
reading raw artifacts; smartz timing includes model cold-loads (first-run effect
present in both systems' first task).
