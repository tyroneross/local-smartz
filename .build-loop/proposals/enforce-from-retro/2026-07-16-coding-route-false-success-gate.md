# Enforce-candidate: coding-route false-success gate

source_run: local-smartz-2026-07-16-settings-enforcement-harness
status: candidate (human review required — never auto-promoted)

## Pattern
Comparison t2 (both legs): the guarded coding route exited 0 in ~10s having
modified nothing, emitting an advisory plan ("Requires human review") for a task
that asked for a fix. Any script/scheduler consuming rc=0 believes work happened.
Contrast: the harness ran pytest before claiming t2 done (its only fully-gated
task — and its only 5/5 integrity score).

## Proposed enforcement
Exit-code semantics: rc=0 requires either (a) workspace mutation + passing
verification (test/doctest/parse of the produced artifact), or (b) an explicit
machine-readable ANALYSIS_ONLY marker + distinct exit code. Applies to
coding_loop/coding_harness. Oracle-gated execution is recommendation #2 in
reports/fable-review-2026-07-16.md.
