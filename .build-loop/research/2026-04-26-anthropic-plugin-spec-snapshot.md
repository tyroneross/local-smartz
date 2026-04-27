# Anthropic Claude Code plugin spec snapshot — 2026-04-26

Source: Context7 `/websites/code_claude` → `code.claude.com/docs/en/plugins`, `/plugins-reference`, `/skills`, `/agent-sdk/slash-commands`.

## plugin.json (manifest)

Lives at `.claude-plugin/plugin.json` ONLY. All other components at plugin root.

| Field | Required? | Type | Notes |
|---|---|---|---|
| `name` | yes | string (kebab-case) | |
| `version` | yes | string (semver) | |
| `description` | yes | string | No documented length floor |
| `author` | **NO (optional)** | object `{name, email?, url?}` | Spec example omits it in minimal manifest |
| `homepage` | no | string (URL) | |
| `repository` | no | string (URL) | |
| `license` | no | string | |
| `keywords` | no | list[string] | |
| `skills` | no | string (path to dir) | |
| `commands` | no | string OR list[string] | path to dir OR list of file paths |
| `agents` | no | string (path to dir) | **agents directory exists at plugin root** |
| `hooks` | no | string (path to hooks.json) | |
| `mcpServers` | no | string (path to mcp config) | |
| `outputStyles` | no | string (path to dir) | |
| `themes` | no | string (path to dir) | |
| `lspServers` | no | string (path to .lsp.json) | |
| `monitors` | no | string (path to monitors.json) | |
| `dependencies` | no | list of strings or `{name, version}` objects | semver range strings |

## Directory layout (correct)

```
my-plugin/
├── .claude-plugin/
│   └── plugin.json      ← only manifest here
├── commands/
├── agents/              ← sub-agent definitions, ROOT level
├── hooks/
├── skills/
├── output-styles/
├── themes/
└── (other component dirs)
```

## SKILL.md frontmatter

| Field | Required? | Notes |
|---|---|---|
| `name` | uncertain | Some examples include it, the minimal example shows ONLY `description`. Likely optional, derived from filename. |
| `description` | yes | When-to-invoke phrasing. No documented length floor. |
| `disable-model-invocation` | no | bool. If true, skill only runs on explicit invocation. |
| `allowed-tools` | no | Space- or comma-separated. Supports `Bash(pattern)` glob matchers, e.g. `Bash(git add *) Bash(git commit *)`. |

Body: free markdown. No documented byte floor.

## Sub-agent (`agents/<name>.md`) frontmatter

| Field | Required? | Notes |
|---|---|---|
| `name` | yes | kebab-case |
| `description` | yes | When Claude should invoke this agent |
| `model` | no | `sonnet` \| `opus` \| `haiku` \| `inherit` \| full model id |
| `effort` | no | `low` \| `medium` \| `high` |
| `maxTurns` | no | positive int |
| `disallowedTools` | no | comma-separated tool names |

Body: free markdown — the agent's system prompt.

```markdown
---
name: agent-name
description: What this agent specializes in and when Claude should invoke it
model: sonnet
effort: medium
maxTurns: 20
disallowedTools: Write, Edit
---

Detailed system prompt for the agent describing its role, expertise, and behavior.
```

## Slash command (`commands/<name>.md`) frontmatter

| Field | Required? | Notes |
|---|---|---|
| `allowed-tools` | **no (optional!)** | Comma-separated. Supports `Bash(pattern)` matchers. |
| `description` | no | Shown in command picker. |
| `model` | no | Full model id like `claude-opus-4-7` accepted. |

Body: command instructions.

## hooks.json events (no schema change vs current localsmartz validator)

`Stop`, `PreCompact`, `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `SubagentStop`, `Notification` — all present in localsmartz's whitelist. ✓

## .mcp.json

Both shapes still supported: `{"mcpServers": {...}}` (preferred) or direct `{name: spec}`. localsmartz validator handles both. ✓

---

## Gaps in `src/localsmartz/plugins/validator.py` vs this spec

| # | Gap | Current behavior | Spec | Severity |
|---|---|---|---|---|
| G1 | No `agents/*.md` discovery + validation | None | Required component type | **Strong checkpoint** |
| G2 | `plugin.json` `author` required | `MISSING_AUTHOR` error | Optional | **Strong checkpoint** |
| G3 | `plugin.json` description min 10 chars | error | No documented floor | Guidance |
| G4 | `plugin.json` ignores `homepage, repository, license, skills, commands, agents, hooks, mcpServers, outputStyles, themes, lspServers, monitors, dependencies` | silently accepted | All optional, shape-checkable | Guidance |
| G5 | `SKILL.md` `name` required | `MISSING_NAME` error | Likely optional | **Strong checkpoint** |
| G6 | `SKILL.md` doesn't accept `allowed-tools`, `disable-model-invocation` | silently accepted (parse_frontmatter doesn't reject unknown keys) | Both are valid optional fields | Guidance |
| G7 | `SKILL.md` description length 80–200 warns outside range | warning | No documented bound | Guidance (relax to >0) |
| G8 | `SKILL.md` body min 100 bytes | error | No documented bound | Guidance |
| G9 | Command `allowed-tools` required | `MISSING_ALLOWED_TOOLS` error | Optional | **Strong checkpoint** |
| G10 | Command doesn't validate `model` field | silently accepted | Optional, accepts model ids | Guidance |
| G11 | `parse_frontmatter` strict YAML — no support for list values, only scalar key:value | scalar only | Spec uses lists for `dependencies`, `keywords` already JSON | parse_frontmatter only used for SKILL.md/commands/agents — those use scalar fields except `allowed-tools` (space-separated string already). OK. |

**Iteration 3 plan implication:** the four **Strong-checkpoint** gaps (G1, G2, G5, G9) MUST land. The Guidance gaps batch into one follow-on commit.
