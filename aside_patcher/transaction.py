"""Stage file replacements and recover pre-operation contents on failure."""

from __future__ import annotations

import os
import shutil
import uuid
from typing import Callable
from pathlib import Path

from .common.exceptions import Refusal
from .common.utils import file_sha256, require_app_stopped, require_replaceable_files, sha256
from .recovery import write_journal


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


def replace_transaction(
    changes: dict[Path, tuple[bytes, bytes]], journal: Path | None = None, guard: Callable[[], None] = require_app_stopped
) -> None:
    """Stage all replacements and roll back completed writes on failure.

    Parameters
    ----------
    changes : dict of Path to tuple of bytes
        Pre-operation and replacement contents for each changed file.
    journal : Path or None
        Optional durable recovery journal anchored in the installation.
    guard : callable
        Browser process guard; scoped by the desktop, global for the legacy CLI.
    """
    require_replaceable_files(changes)
    staged, rollback, completed = {}, {}, []
    rollback_failed = False
    resolved = False
    journal_owned = False
    record = {"schema_version": 1, "phase": "prepared", "entries": []}
    try:
        for path, (before, after) in changes.items():
            staged[path] = stage_replacement(path, after, "new")
            rollback[path] = stage_replacement(path, before, "rollback")
        guard()
        validate_staged_inputs(changes)
        journal_owned = _prepare_journal(journal, changes, staged, rollback, record)
        for path, (_, after) in changes.items():
            os.replace(staged[path], path)
            completed.append(path)
            if file_sha256(path) != sha256(after):
                raise Refusal(f"Replacement verification failed: {path}")
        _commit_journal(journal, journal_owned, record)
        resolved = True
    except (OSError, Refusal) as exc:
        errors = rollback_completed(changes, rollback, completed)
        rollback_failed = bool(errors)
        resolved = not rollback_failed
        detail = "; rollback failed: " + "; ".join(errors) if errors else "; pre-operation files preserved"
        raise Refusal(f"Installation failed: {exc}{detail}") from exc
    finally:
        _cleanup_transaction(journal, journal_owned, resolved, rollback_failed, staged=staged, rollback=rollback)


def _prepare_journal(journal, changes, staged, rollback, record):
    """Persist all recovery locations before the first replacement.

    Parameters
    ----------
    journal : Path or None
        Optional journal path.
    changes : dict
        Verified before/after bytes.
    staged : dict
        Forward staging paths.
    rollback : dict
        Recovery staging paths.
    record : dict
        Mutable transaction phase record.

    Returns
    -------
    bool
        Whether this transaction owns a durable journal.
    """
    if not journal or not changes:
        return False
    if journal.exists():
        raise Refusal("Pending recovery must be completed before installation.")
    record["entries"] = [
        {
            "path": str(path.resolve()),
            "before": sha256(before),
            "after": sha256(after),
            "new": str(staged[path].resolve()),
            "rollback": str(rollback[path].resolve()),
        }
        for path, (before, after) in changes.items()
    ]
    write_journal(journal, record)
    return True


def _commit_journal(journal, owned, record):
    """Record verified completion before discarding recovery material.

    Parameters
    ----------
    journal : Path or None
        Optional journal path.
    owned : bool
        Whether this transaction wrote the journal.
    record : dict
        Mutable transaction phase record.
    """
    if owned:
        record["phase"] = "committed"
        write_journal(journal, record)


def _cleanup_transaction(journal, owned, resolved, rollback_failed, *, staged, rollback):  # pylint: disable=too-many-arguments
    """Retain recovery material after a crash or incomplete rollback.

    Parameters
    ----------
    journal : Path or None
        Optional journal path.
    owned : bool
        Whether this transaction owns that journal.
    resolved : bool
        Whether completion or rollback finished.
    rollback_failed : bool
        Whether a recovery copy still needs manual attention.
    staged : dict
        Forward staging paths.
    rollback : dict
        Recovery staging paths.
    """
    if resolved or not journal or not journal.exists():
        for path in staged.values():
            path.unlink(missing_ok=True)
        if not rollback_failed:
            for path in rollback.values():
                path.unlink(missing_ok=True)
    if owned and resolved:
        journal.unlink(missing_ok=True)


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
