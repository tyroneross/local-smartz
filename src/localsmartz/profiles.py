"""Hardware profile detection and configuration."""

import platform
import subprocess


PROFILES = {
    "full": {
        "planning_model": "llama3.1:70b-instruct-q5_K_M",
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


def get_profile(name: str | None = None) -> dict:
    """Get profile configuration by name or auto-detect.

    Args:
        name: Profile name ("full" or "lite"), or None to auto-detect

    Returns:
        Profile configuration dict with "name" key added
    """
    if name is None:
        name = detect_profile()

    if name not in PROFILES:
        raise ValueError(f"Unknown profile: {name}. Available: {list(PROFILES.keys())}")

    profile = PROFILES[name].copy()
    profile["name"] = name
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
