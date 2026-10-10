"""Guard binary replacement against a running Windows Aside process."""

import os
import subprocess

from .core import Refusal


def aside_is_running() -> bool:
    """Check the Windows process list for Aside.

    Returns
    -------
    bool
        Whether Aside is running; False on non-Windows systems.
    """
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Aside.exe", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as exc:
        raise Refusal(f"Could not determine whether Aside is running: {exc}") from exc
    if result.returncode != 0:
        raise Refusal("Could not determine whether Aside is running; refusing to modify the DLL.")
    return "aside.exe" in result.stdout.casefold()


def require_app_stopped() -> None:
    """Refuse file modification while Aside is running."""
    if aside_is_running():
        raise Refusal("Aside is running. Close it completely, then retry.")
