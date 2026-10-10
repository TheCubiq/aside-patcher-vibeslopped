"""Validated multi-file mods and rollback of an interrupted replacement."""

from __future__ import annotations

from pathlib import Path

from .common.exceptions import Refusal
from .common.models import Installation
from .common.utils import backup_directory, file_sha256, preserve_original, read_binary, report_status, require_app_stopped, sha256
from .manifest import validate_component
from .recipes import render
from .transaction import replace_transaction


def run_installation(build: Installation, command: str, targets: list[str] | None = None) -> int:
    """Inspect, apply, or restore a verified multi-file installation.

    Parameters
    ----------
    build : Installation
        Supported build definition and hash allowlist.
    command : str
        Operation: status, apply, or restore.
    targets : sequence of str
        Selected patch target names.

    Returns
    -------
    int
        Exit code: zero on success, or two for an unsupported state or refusal.
    """
    if command != "status":
        require_app_stopped()
    validate_component(build.component)
    hashes = {name: file_sha256(f.path) for name, f in build.files.items()}
    if command == "status":
        return report_status(build, hashes)
    current = build.identify(hashes)
    desired = () if command == "restore" else build.select(current, targets or [])
    if desired == current:
        print("Requested state is already installed.")
        return 0
    changes = prepare_changes(build, hashes, desired)
    replace_transaction(changes)
    print("Installed: " + (", ".join(desired) if desired else "vendor originals"))
    return 0


def prepare_changes(build: Installation, hashes: dict[str, str], desired: tuple[str, ...]) -> dict[Path, tuple[bytes, bytes]]:
    """Preserve originals and render every replacement before installation.

    Parameters
    ----------
    build : Installation
        Supported installation definition and complete hash allowlist.
    hashes : dict[str, str]
        Observed hashes for every installed artifact.
    desired : tuple[str, ...]
        Verified target combination to render.

    Returns
    -------
    dict[Path, tuple[bytes, bytes]]
        Verified pre-operation and replacement contents for changed files only.
    """
    root = backup_directory()
    changes = {}
    for name, artifact in build.files.items():
        before = read_binary(artifact.path)
        if sha256(before) != hashes[name]:
            raise Refusal(f"File changed during validation: {artifact.path}")
        backup = root / f"{artifact.original_sha256}{artifact.path.suffix}"
        original = before if hashes[name] == artifact.original_sha256 else read_binary(backup)
        preserve_original(original, artifact, root)
        after = render(original, artifact, desired)
        if sha256(after) != build.states[desired][name]:
            raise Refusal(f"Result hash is not verified for {artifact.path.name}.")
        if before != after:
            changes[artifact.path] = (before, after)
    return changes
