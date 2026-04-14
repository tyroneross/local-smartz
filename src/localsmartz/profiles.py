"""Hardware profile detection and configuration."""

import platform
import subprocess


# Per-agent role descriptions surfaced to the UI and (when "single agent mode"
# is active) injected into the system prompt so the LLM stays in role.
AGENT_ROLES = {
    "planner": {
        "title": "Planner",
        "summary": "Decomposes the question into steps. Owns the to-do list.",
        # Custom tools this role is allowed to call (beyond DeepAgents
        # built-ins like write_todos, ls, read_file, write_file, edit_file,
        # glob, grep — those are always provided by the middleware stack).
        # The Planner has an EMPTY custom list: it has nothing to do
        # except write todos, and the tighter surface blocks the class of
        # tool-call hallucinations we saw in practice (small models
        # inventing namespaced tools like ``repo_browser.write_todos``
        # when given too many options).
        "tools": [],
        "system_focus": (
            "You are the PLANNER agent. For multi-step tasks, use write_todos "
            "to lay out a concrete, ordered list of steps and stop. "
            "For a simple factual question that does not require research, "
            "answer it directly in one or two sentences — do NOT call any tools. "
            "Never invent tool namespaces (no dots in tool names)."
        ),
    },
    "researcher": {
        "title": "Researcher",
        "summary": "Gathers information from the web and local files.",
        "tools": [
            "web_search",
            "scrape_url",
            "parse_pdf",
            "read_spreadsheet",
            "read_text_file",
            "write_file",
            "read_file",
        ],
        "system_focus": (
            "You are the RESEARCHER agent. Use web_search, scrape_url, parse_pdf, "
            "read_text_file, and read_spreadsheet to gather raw information. "
            "Do not analyze or write a report — collect sources and key findings, "
            "save them with write_file, and stop."
        ),
    },
    "analyzer": {
        "title": "Analyzer",
        "summary": "Computes, calculates, and reasons over data.",
        "tools": ["python_exec", "read_file", "write_file", "ls"],
        "system_focus": (
            "You are the ANALYZER agent. Use python_exec for ALL computation, "
            "statistics, and data manipulation. Read prior research from disk with "
            "read_file. Output structured findings — no narrative writing."
        ),
    },
    "writer": {
        "title": "Writer",
        "summary": "Composes the final report or answer using pyramid principle.",
        "tools": ["create_report", "create_spreadsheet", "read_file", "ls"],
        # Pyramid-principle short-form is baked directly into the prompt —
        # Claude Code skills can't be invoked mid-turn by a Python agent
        # (DeepAgents' SkillsMiddleware injects only at startup). Lead with
        # the governing thought so the reader gets the answer first.
        "system_focus": (
            "You are the WRITER agent. Compose the final user-facing answer. "
            "Use pyramid-principle short-form: 1) lead with the GOVERNING THOUGHT "
            "(one sentence answering the user's question directly); 2) follow with "
            "2–4 MECE KEY LINES (mutually exclusive, collectively exhaustive); "
            "3) provide SUPPORT (numbers, sources, caveats) under each key line. "
            "Use create_report for deliverables. Pull facts via read_file. "
            "Do not run new searches or computations."
        ),
    },
    # Mid-pipeline fact-checker (reshaped from the former ``reviewer`` role
    # per the design decision to collapse the two). Returns a structured
    # JSON verdict the orchestrator can act on — this is what enables the
    # re-dispatch loop (``needs_more`` → bounce back to researcher with the
    # missing facts). Keeps read-only tools plus web_search so it can
    # spot-verify claims instead of trusting prior research.
    "fact_checker": {
        "title": "Fact-checker",
        "summary": "Validates mid-pipeline findings; returns JSON verdict.",
        "tools": ["read_file", "ls", "web_search"],
        "system_focus": (
            "You are the FACT-CHECKER agent. Read the latest researcher/analyzer "
            "output and validate it. Spot-verify any claim that looks uncertain "
            "with web_search. Return a single JSON object with exactly this shape:\n"
            '  {"verdict": "ok" | "needs_more", "missing_facts": [string, ...]}\n'
            "Use \"needs_more\" only when there are specific, nameable gaps. "
            "Do NOT rewrite or summarize — your job is the verdict, nothing else."
        ),
    },
    # The orchestrator IS the main agent when no focus is pinned. Terse
    # by design (≤ 180 tokens) — qwen3:8b hallucinates tool names when the
    # system prompt grows. No tools of its own; it only routes via ``task``.
    "orchestrator": {
        "title": "Orchestrator",
        "summary": "Routes queries; decides direct answer vs specialist delegation.",
        "tools": [],
        "system_focus": (
            "You are the ORCHESTRATOR. Route the user's query:\n"
            "- Trivial factual question → answer in 1–2 sentences, no tool calls.\n"
            "- Single-facet question → call task(<role>) once.\n"
            "- Multi-facet question → emit MULTIPLE task(<role>) calls in the "
            "SAME turn for parallel execution.\n\n"
            "After specialists return, ALWAYS call task(\"fact_checker\"). "
            "If it returns {\"verdict\":\"needs_more\"}, call task(\"researcher\") "
            "again with the missing_facts as your instruction (max 2 extra rounds). "
            "Then call task(\"writer\") for the final synthesis.\n\n"
            "Roles available: researcher (web + files), analyzer (python_exec), "
            "fact_checker (verdict JSON), writer (pyramid-principle synthesis). "
            "Never invent tool namespaces (no dots in tool names)."
        ),
    },
}


def agent_tool_names(role: str) -> list[str]:
    """Return the tool names allowed for a given role, or an empty list if
    the role isn't defined. Used both by the agent builder and by ``/api/agents``
    so the UI can show "Planner uses: write_todos"."""
    meta = AGENT_ROLES.get(role)
    if not isinstance(meta, dict):
        return []
    tools = meta.get("tools")
    if isinstance(tools, list):
        return [str(t) for t in tools]
    return []


# Profile-level agent definitions. Each entry maps agent-name → {model, summary}.
# The `summary` mirrors AGENT_ROLES[name]["summary"] so list_agents can surface
# the same copy even if the caller hasn't loaded AGENT_ROLES directly. Models
# here are the profile defaults; user overrides via
# global_config["agent_models"] take precedence at resolution time.
PROFILES = {
    "full": {
        # Fast planning model — a lightweight model for planning + first-token
        # latency dominates simple-query timing, so this is qwen3:8b (5 GB).
        # Execution still uses a strong 32B coder model for heavy lifting.
        # Users can override via the toolbar picker or `localsmartz --model`.
        "planning_model": "qwen3:8b-q4_K_M",
        "execution_model": "qwen2.5-coder:32b-instruct-q5_K_M",
        "agents": {
            "planner": {
                "model": "qwen3:8b-q4_K_M",
                "summary": AGENT_ROLES["planner"]["summary"],
            },
            "researcher": {
                # Pinned to the 32B coder model to match analyzer/fact_checker/writer.
                # The graph pipeline goes researcher → analyzer → fact_checker → writer;
                # when researcher was 8B, every round paid a full 32B VRAM load on the
                # next hop. All four roles sharing one model = zero mid-round swaps on
                # machines that meet the full-profile RAM bar (>=64 GB).
                "model": "qwen2.5-coder:32b-instruct-q5_K_M",
                "summary": AGENT_ROLES["researcher"]["summary"],
            },
            "analyzer": {
                "model": "qwen2.5-coder:32b-instruct-q5_K_M",
                "summary": AGENT_ROLES["analyzer"]["summary"],
            },
            "writer": {
                "model": "qwen2.5-coder:32b-instruct-q5_K_M",
                "summary": AGENT_ROLES["writer"]["summary"],
            },
            "fact_checker": {
                "model": "qwen2.5-coder:32b-instruct-q5_K_M",
                "summary": AGENT_ROLES["fact_checker"]["summary"],
            },
            "orchestrator": {
                "model": "qwen2.5-coder:32b-instruct-q5_K_M",
                "summary": AGENT_ROLES["orchestrator"]["summary"],
            },
        },
        "max_concurrent_agents": 2,
        "max_turns": 20,
        "quality_review": True,
        "subagent_delegation": True,
    },
    "lite": {
        "planning_model": "qwen3:8b-q4_K_M",
        "execution_model": "qwen3:8b-q4_K_M",
        "agents": {
            "planner": {
                "model": "qwen3:8b-q4_K_M",
                "summary": AGENT_ROLES["planner"]["summary"],
            },
            "researcher": {
                "model": "qwen3:8b-q4_K_M",
                "summary": AGENT_ROLES["researcher"]["summary"],
            },
            "analyzer": {
                "model": "qwen3:8b-q4_K_M",
                "summary": AGENT_ROLES["analyzer"]["summary"],
            },
            "writer": {
                "model": "qwen3:8b-q4_K_M",
                "summary": AGENT_ROLES["writer"]["summary"],
            },
        },
        "max_concurrent_agents": 1,
        "max_turns": 10,
        "quality_review": False,
        "subagent_delegation": False,
    },
}


def _get_agent_overrides() -> dict[str, str]:
    """Read per-agent model overrides from global_config.

    Returns {} if unset or on any error — never crashes profile construction.
    """
    try:
        from localsmartz import global_config  # local import to avoid cycles at module-load

        overrides = global_config.get("agent_models")
        if isinstance(overrides, dict):
            return {
                str(k): str(v)
                for k, v in overrides.items()
                if isinstance(v, str) and v.strip()
            }
    except Exception:
        pass
    return {}


def _agents_dict(profile: dict) -> dict[str, dict]:
    """Normalize ``profile['agents']`` into the dict shape.

    Accepts the new dict[str, dict] shape directly. Legacy list[str] values
    (in case a caller built a profile by hand) are upgraded on the fly using
    AGENT_ROLES defaults + the profile's planning model.
    """
    agents = profile.get("agents", {})
    if isinstance(agents, dict):
        return agents
    # Legacy tolerance: list[str] -> dict[str, dict]
    fallback_model = profile.get("planning_model", "")
    out: dict[str, dict] = {}
    for name in agents:
        meta = AGENT_ROLES.get(name, {})
        out[name] = {
            "model": fallback_model,
            "summary": meta.get("summary", ""),
        }
    return out


def list_agents(profile: dict) -> list[dict]:
    """Return the agents for a profile, decorated with title + summary + model.

    Merges per-user overrides from global_config["agent_models"] so the
    returned model field is the *effective* model used at run time.

    Shape: ``[{"name", "title", "summary", "model", "tools"}, ...]``

    **Filters out main-agent-only roles** (the orchestrator) so the UI
    doesn't surface them as pickable focus agents. Orchestrator is the
    default multi-agent path, not a specialist the user can pin — if it
    appeared in the sidebar and the user clicked it, ``focus_agent="orchestrator"``
    would reach ``create_agent``, which scopes the main agent to the
    orchestrator's empty tool list — locking out delegation entirely and
    producing an infinite "Thinking…" with no output.
    """
    # Mirror of agent._MAIN_AGENT_ONLY. Duplicated here instead of imported
    # to avoid a circular import with the agent module.
    _MAIN_AGENT_ONLY = {"orchestrator"}

    overrides = _get_agent_overrides()
    out: list[dict] = []
    for name, spec in _agents_dict(profile).items():
        if name in _MAIN_AGENT_ONLY:
            continue
        meta = AGENT_ROLES.get(name, {})
        default_model = spec.get("model", "") if isinstance(spec, dict) else ""
        summary = ""
        if isinstance(spec, dict):
            summary = spec.get("summary", "") or meta.get("summary", "")
        else:
            summary = meta.get("summary", "")
        out.append({
            "name": name,
            "title": meta.get("title", name.title()),
            "summary": summary,
            "model": overrides.get(name, default_model),
            # Tool allow-list from AGENT_ROLES — surfaced to the UI so a
            # sidebar can render "Planner uses: write_todos" without the
            # Swift app having to know about profile internals.
            "tools": agent_tool_names(name),
            # Full role-specific system prompt — surfaced read-only in the
            # Settings → Agents tab so users can inspect what each agent is
            # actually being told. Empty string when the role has no custom
            # focus prompt (rather than None) so consumers can treat it as
            # a plain string.
            "system_focus": meta.get("system_focus", ""),
        })
    return out


def effective_agent_models(profile: dict) -> dict[str, str]:
    """Return the effective model-per-agent map (profile default + user override).

    Keys are agent names; values are the model string that would actually be
    used if that agent is focused.
    """
    overrides = _get_agent_overrides()
    agents = _agents_dict(profile)
    return {
        name: overrides.get(name, spec.get("model", "") if isinstance(spec, dict) else "")
        for name, spec in agents.items()
    }


def get_agent_model(profile: dict, agent_name: str) -> str | None:
    """Return the effective model for one agent (profile default + user override).

    Returns None if the agent isn't defined in this profile.
    """
    if not agent_name:
        return None
    agents = _agents_dict(profile)
    if agent_name not in agents:
        return None
    overrides = _get_agent_overrides()
    if agent_name in overrides:
        return overrides[agent_name]
    spec = agents[agent_name]
    if isinstance(spec, dict):
        return spec.get("model") or None
    return None


def detect_profile() -> str:
    """Detect hardware profile based on system RAM.

    Returns "full" if >= 64GB RAM, "lite" otherwise.
    """
    try:
        system = platform.system()

        if system == "Darwin":  # macOS
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                check=True,
            )
            ram_bytes = int(result.stdout.strip())
        elif system == "Linux":
            import os
            ram_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        else:
            # Unknown system, default to lite
            return "lite"

        # Convert to GB
        ram_gb = ram_bytes / (1024 ** 3)

        return "full" if ram_gb >= 64 else "lite"

    except Exception:
        # On error, default to lite profile
        return "lite"


def get_profile(name: str | None = None, model_override: str | None = None) -> dict:
    """Get profile configuration by name or auto-detect.

    Args:
        name: Profile name ("full" or "lite"), or None to auto-detect
        model_override: If set, replaces planning_model (user-selected model)

    Returns:
        Profile configuration dict with "name" key added
    """
    if name is None:
        name = detect_profile()

    if name not in PROFILES:
        raise ValueError(f"Unknown profile: {name}. Available: {list(PROFILES.keys())}")

    # Deep-ish copy so callers can't mutate the module-level PROFILES dict.
    src = PROFILES[name]
    profile = {k: v for k, v in src.items()}
    # Copy the nested agents dict so edits never leak back.
    if isinstance(src.get("agents"), dict):
        profile["agents"] = {
            agent_name: dict(spec) if isinstance(spec, dict) else spec
            for agent_name, spec in src["agents"].items()
        }
    profile["name"] = name

    if model_override:
        profile["planning_model"] = model_override

    return profile


_FAST_PATH_FACTUAL_PREFIXES: tuple[str, ...] = (
    "what is ", "what's ", "whats ",
    "who is ", "who's ", "whos ",
    "when did ", "when was ", "when is ",
    "where is ", "where's ", "where are ", "where did ",
    "how many ", "how much ", "how old ", "how tall ",
    "define ", "definition of ", "meaning of ",
    "capital of ", "population of ", "name of ",
)


def is_fast_path(prompt: str) -> bool:
    """True if the prompt looks trivial enough to skip the agent graph.

    Heuristics:
    - Under 400 chars
    - No more than 2 sentence terminators (period or question mark)
    - Positive short-circuit: if the prompt starts with a factual-question
      prefix (``what is``/``who is``/``when did``/``capital of`` etc.), it's
      fast-path regardless of research keywords. This rescues short single-
      clause questions whose subject happens to include a research-y word.
    - Otherwise: must not contain any research-oriented keyword.
    """
    if not isinstance(prompt, str):
        return False
    if len(prompt) > 400:
        return False
    t = prompt.lower().strip()
    if not t:
        return False
    # Size/terminator caps apply to the positive short-circuit too.
    if t.count(".") + t.count("?") > 2:
        return False
    # Positive short-circuit for short factual-question shapes.
    if any(t.startswith(p) for p in _FAST_PATH_FACTUAL_PREFIXES):
        return True
    research_keywords = [
        "research", "analyze", "compare", "report", "write a", "summarize",
        "investigate", "find out", "look into", "deep dive", "explore",
        "evaluate", "assess", "benchmark", "survey", "breakdown", "scrape",
        "search the web", "find sources", "pull data",
    ]
    if any(k in t for k in research_keywords):
        return False
    return True


def get_model(profile: dict, role: str) -> str:
    """Get model string for a specific role.

    Args:
        profile: Profile configuration dict
        role: Either "planning" or "execution"

    Returns:
        Model string for the requested role
    """
    if role == "planning":
        return profile["planning_model"]
    elif role == "execution":
        return profile["execution_model"]
    else:
        raise ValueError(f"Unknown role: {role}. Expected 'planning' or 'execution'")
