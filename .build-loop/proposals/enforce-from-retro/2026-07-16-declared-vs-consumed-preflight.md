# Enforce-candidate: declared-vs-consumed state preflight

source_run: local-smartz-2026-07-16-settings-enforcement-harness
status: candidate (human review required — never auto-promoted)

## Pattern (3 same-root defects, one run)
1. `active_model` written by Settings UI, read by nothing (orphan setting).
2. Agent prompt `.md` files edited+displayed by UI, runtime read embedded copies.
3. `--check` reported an uninstalled model tag "ready"; runtime 404ed.

Shared root: **declared/written state trusted over actually-consumed/actually-
installed state.** Writers and displays validated the write; nothing validated
the read path.

## Proposed enforcement
Preflight family: for every user-editable setting/artifact, a test asserting the
RUNTIME consumer observes the edit (write → resolve → assert effective value),
not just that the write persisted. Shape exists now in
tests/test_prompt_routing.py and tests/test_model_precedence.py — generalize as
a checklist item in plan templates: "every settings key names its runtime
consumer + a consumption test."
