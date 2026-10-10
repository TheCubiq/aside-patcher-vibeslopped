"""Stage file replacements and recover pre-operation contents on failure."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

from .common.exceptions import Refusal
from .common.utils import file_sha256, require_app_stopped, sha256


def stage_replacement(path: Path, data: bytes, label: str) -> Path:
    """Write and flush a temporary replacement beside the installed file.

    Parameters
    ----------
    path : Path
        File to read or replace.
    data : bytes
        Bytes to inspect or write.
    label : str
        Suffix identifying a forward or rollback staging file.

    Returns
    -------
    Path
        Flushed staging file with copied file metadata.
    """
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.{label}")
    try:
        with staged.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copystat(path, staged)
        return staged
    except OSError:
        staged.unlink(missing_ok=True)
        raise


def replace_transaction(changes: dict[Path, tuple[bytes, bytes]]) -> None:
    """Stage all replacements and roll back completed writes on failure.

    Parameters
    ----------
    changes : dict of Path to tuple of bytes
        Pre-operation and replacement contents for each changed file.
    """
    staged, rollback, completed = {}, {}, []
    rollback_failed = False
    try:
        for path, (before, after) in changes.items():
            staged[path] = stage_replacement(path, after, "new")
            rollback[path] = stage_replacement(path, before, "rollback")
        require_app_stopped()
        validate_staged_inputs(changes)
        for path, (_, after) in changes.items():
            os.replace(staged[path], path)
            completed.append(path)
            if file_sha256(path) != sha256(after):
                raise Refusal(f"Replacement verification failed: {path}")
    except (OSError, Refusal) as exc:
        errors = rollback_completed(changes, rollback, completed)
        rollback_failed = bool(errors)
        detail = "; rollback failed: " + "; ".join(errors) if errors else "; pre-operation files preserved"
        raise Refusal(f"Installation failed: {exc}{detail}") from exc
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
        if not rollback_failed:
            for path in rollback.values():
                path.unlink(missing_ok=True)


def rollback_completed(changes: dict[Path, tuple[bytes, bytes]], rollback: dict[Path, Path], completed: list[Path]) -> list[str]:
    """Restore completed replacements and collect failures with recovery paths.

    Parameters
    ----------
    changes : dict[Path, tuple[bytes, bytes]]
        Original and replacement contents keyed by installed path.
    rollback : dict[Path, Path]
        Staged pre-operation recovery files keyed by installed path.
    completed : list[Path]
        Successfully replaced paths in installation order.

    Returns
    -------
    list[str]
        Rollback failures with preserved recovery-copy locations.
    """
    errors = []
    for path in reversed(completed):
        try:
            os.replace(rollback[path], path)
            if file_sha256(path) != sha256(changes[path][0]):
                raise Refusal("Rollback hash mismatch.")
        except (OSError, Refusal) as exc:
            errors.append(f"{path}: {exc}; recovery copy: {rollback[path]}")
    return errors


def validate_staged_inputs(changes: dict[Path, tuple[bytes, bytes]]) -> None:
    """Refuse installation if any input changed while staging.

    Parameters
    ----------
    changes : dict[Path, tuple[bytes, bytes]]
        Original and replacement contents keyed by installed path.
    """
    for path, (before, _) in changes.items():
        if file_sha256(path) != sha256(before):
            raise Refusal(f"File changed while staging: {path}")
