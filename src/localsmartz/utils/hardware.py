"""Hardware detection utilities."""

import os
import platform
import subprocess


def get_ram_gb() -> int:
    """Detect system RAM in GB. Returns 0 on failure."""
    try:
        if platform.system() == "Darwin":
            try:
                ram_bytes = int(subprocess.check_output(
                    ["sysctl", "-n", "hw.memsize"], text=True
                ).strip())
            except Exception:
                # Sandboxed macOS environments can deny ``sysctl hw.memsize``
                # even though POSIX ``sysconf`` still exposes physical pages.
                ram_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        else:
            ram_bytes = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        return int(ram_bytes / (1024 ** 3))
    except Exception:
        return 0
