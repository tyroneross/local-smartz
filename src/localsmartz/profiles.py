"""Hardware profile detection and configuration."""

import platform
import subprocess


PROFILES = {
    "full": {
        # Fast planning model — a lightweight model for planning + first-token
        # latency dominates simple-query timing, so this is qwen3:8b (5 GB).
        # Execution still uses a strong 32B coder model for heavy lifting.
        # Users can override via the toolbar picker or `localsmartz --model`.
        "planning_model": "qwen3:8b-q4_K_M",
        "execution_model": "qwen2.5-coder:32b-instruct-q5_K_M",
        "agents": ["planner", "researcher", "analyzer", "writer", "reviewer"],
        "max_concurrent_agents": 2,
        "max_turns": 20,
        "quality_review": True,
        "subagent_delegation": True,
    },
    "lite": {
        "planning_model": "qwen3:8b-q4_K_M",
        "execution_model": "qwen3:8b-q4_K_M",
        "agents": ["planner", "researcher", "analyzer", "writer"],
        "max_concurrent_agents": 1,
        "max_turns": 10,
        "quality_review": False,
        "subagent_delegation": False,
    },
}


# Per-agent role descriptions surfaced to the UI and (when "single agent mode"
# is active) injected into the system prompt so the LLM stays in role.
AGENT_ROLES = {
    "planner": {
        "title": "Planner",
        "summary": "Decomposes the question into steps. Owns the to-do list.",
        "system_focus": (
            "You are the PLANNER agent. Your single job is to break the user's question "
            "into a concrete, ordered list of steps. Use write_todos. Do NOT execute "
            "research or compute results yourself — just plan. End by listing the steps."
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


def list_agents(profile: dict) -> list[dict]:
    """Return the agents for a profile, decorated with title + summary."""
    out = []
    for name in profile.get("agents", []):
        meta = AGENT_ROLES.get(name)
        if meta is None:
            out.append({
                "name": name,
                "title": name.title(),
                "summary": "",
            })
        else:
            out.append({
                "name": name,
                "title": meta["title"],
                "summary": meta["summary"],
            })
    return out


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

    profile = PROFILES[name].copy()
    profile["name"] = name

    if model_override:
        profile["planning_model"] = model_override

    return profile


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
