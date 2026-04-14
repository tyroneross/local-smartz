"""Hardware profile detection and configuration."""

import platform
import subprocess


# Per-agent role descriptions surfaced to the UI and (when "single agent mode"
# is active) injected into the system prompt so the LLM stays in role.
AGENT_ROLES = {
    "planner": {
        "title": "Planner",
        "summary": "Decomposes the question into steps. Owns the to-do list.",
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
        "system_focus": (
            "You are the ANALYZER agent. Use python_exec for ALL computation, "
            "statistics, and data manipulation. Read prior research from disk with "
            "read_file. Output structured findings — no narrative writing."
        ),
    },
    "writer": {
        "title": "Writer",
        "summary": "Composes the final report or answer.",
        "system_focus": (
            "You are the WRITER agent. Compose the final user-facing answer or report. "
            "Use create_report when the user wants a deliverable. Pull facts from prior "
            "files via read_file. Do not run new searches or computations."
        ),
    },
    "reviewer": {
        "title": "Reviewer",
        "summary": "Critiques the output for accuracy and clarity.",
        "system_focus": (
            "You are the REVIEWER agent. Read the most recent answer or report and "
            "produce a critique: factual issues, missing context, unclear claims, "
            "structural problems. Do not rewrite — review and recommend."
        ),
    },
}


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
                "model": "qwen3:8b-q4_K_M",
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
            "reviewer": {
                "model": "qwen2.5-coder:32b-instruct-q5_K_M",
                "summary": AGENT_ROLES["reviewer"]["summary"],
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

    Shape: ``[{"name", "title", "summary", "model"}, ...]``
    """
    overrides = _get_agent_overrides()
    out: list[dict] = []
    for name, spec in _agents_dict(profile).items():
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


def agent_focus_prompt(agent_name: str | None) -> str:
    """Return a system prompt suffix that pins the LLM to one role.

    Empty when agent_name is None or unknown — the default multi-agent flow runs.
    """
    if not agent_name:
        return ""
    meta = AGENT_ROLES.get(agent_name)
    if not meta:
        return ""
    return (
        "\n\n## Single-Agent Mode\n\n"
        f"{meta['system_focus']}\n\n"
        "Do not delegate via the task tool. Do the work in your own scope and stop."
    )


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


def is_fast_path(prompt: str) -> bool:
    """True if the prompt looks trivial enough to skip the agent graph.

    Heuristics (all must pass):
    - Under 400 chars
    - No research-oriented keywords
    - No more than 2 sentence terminators (period or question mark)
    """
    if not isinstance(prompt, str):
        return False
    if len(prompt) > 400:
        return False
    t = prompt.lower().strip()
    if not t:
        return False
    research_keywords = [
        "research", "analyze", "compare", "report", "write a", "summarize",
        "investigate", "find out", "look into", "deep dive", "explore",
        "evaluate", "assess", "benchmark", "survey", "breakdown", "scrape",
        "search the web", "find sources", "pull data",
    ]
    if any(k in t for k in research_keywords):
        return False
    # Multi-sentence prompts usually imply composition
    if t.count(".") + t.count("?") > 2:
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
