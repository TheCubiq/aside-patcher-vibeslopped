"""Validated multi-file mods and rollback of an interrupted replacement."""

from __future__ import annotations

from .common.exceptions import Refusal
from .common.models import Installation
from .common.utils import file_sha256, report_status, require_app_stopped, validate_component
from .recipes import prepare_changes, validate_component_change
from .transaction import replace_transaction


def run_installation(build: Installation, command: str, targets: list[str] | None = None) -> int:
    """Inspect, apply, or restore a verified multi-file installation.

    Parameters
    ----------
    build : Installation
        Supported build definition and hash allowlist.
    command : str
        Operation: status, enable, disable, or restore.
    targets : sequence of str
        Selected patch target names.

    Returns
    -------
    int
        Exit code: zero on success, or two for an unsupported state or refusal.
    """
    if command != "status":
        require_app_stopped()
    hashes = {name: file_sha256(f.path) for name, f in build.files.items()}
    if command == "status":
        validate_component(build.component)
        return report_status(build, hashes)
    current = build.identify(hashes)
    desired = requested_state(build, current, command, targets or [])
    validate_component_change(build, hashes, desired)
    if desired == current and hashes == build.states[desired]:
        print("Requested state is already installed.")
        return 0
    changes = prepare_changes(build, hashes, desired)
    replace_transaction(changes)
    print("Installed: " + (", ".join(desired) if desired else "vendor originals"))
    return 0


def requested_state(build: Installation, current: tuple[str, ...], command: str, targets: list[str]) -> tuple[str, ...]:
    """Choose the verified target state for enable, disable, or restore.

    Parameters
    ----------
    build : Installation
        Supported file states and patch conflicts.
    current : tuple[str, ...]
        Currently installed patch IDs.
    command : str
        Requested mutation command.
    targets : list[str]
        Patch IDs supplied on the command line.

    Returns
    -------
    tuple[str, ...]
        Verified desired patch combination.
    """
    if command == "restore":
        return ()
    if command in {"enable", "disable"}:
        return build.choose(current, targets, enable=command == "enable")
    raise Refusal(f"Unknown installation command: {command}")
