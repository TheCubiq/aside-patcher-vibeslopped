"""Durable transaction journal for interrupted, explicitly scoped installations."""

import json
import os
from pathlib import Path

from .common.exceptions import Refusal
from .common.models import Installation
from .common.utils import file_sha256


def write_journal(path: Path, record: dict) -> None:
    """Atomically flush a recovery record before modifying browser files.

    Parameters
    ----------
    path : Path
        Protected journal beside the selected browser DLL.
    record : dict
        Prepared or committed transaction record.
    """
    temporary = path.with_suffix(".writing")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def recover(build: Installation, path: Path) -> bool:
    """Recover only reviewed artifacts from an interrupted operation.

    Parameters
    ----------
    build : Installation
        Reviewed artifact paths and accepted hashes.
    path : Path
        Journal anchored in the selected installation.

    Returns
    -------
    bool
        Whether a pending journal was recovered or cleaned.
    """
    if not path.exists():
        return False
    raw, expected = _read_record(build, path)
    entries = raw["entries"]
    # Validate every entry before any recovery writes.
    for entry in entries:
        _validate_entry(entry, expected, raw["phase"])
    _restore_entries(entries, raw["phase"])
    for entry in entries:
        for field in ("new", "rollback"):
            Path(entry[field]).unlink(missing_ok=True)
    path.unlink()
    return True


def _read_record(build, path):
    """Read journal structure and the complete reviewed recovery scope.

    Parameters
    ----------
    build : Installation
        Reviewed artifact paths and hashes.
    path : Path
        Existing journal path.

    Returns
    -------
    tuple
        Record and accepted hash sets.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or raw.get("phase") not in ("prepared", "committed"):
        raise Refusal("Unknown recovery journal format; retain it for inspection.")
    expected = {}
    for name, artifact in build.files.items():
        expected[str(artifact.path.resolve())] = {hashes[name] for _, hashes in tuple(build.states.items()) + build.previous_states}
    entries = raw.get("entries", [])
    if not entries or len({entry["path"] for entry in entries}) != len(entries):
        raise Refusal("Invalid recovery journal artifact list.")
    return raw, expected


def _restore_entries(entries, phase):
    """Reverse only prepared writes after the entire journal was validated.

    Parameters
    ----------
    entries : list
        Validated artifact records.
    phase : str
        Prepared or committed transaction phase.
    """
    for entry in reversed(entries):
        target = Path(entry["path"])
        if phase == "prepared" and file_sha256(target) != entry["before"]:
            os.replace(entry["rollback"], target)
            if file_sha256(target) != entry["before"]:
                raise Refusal("Recovery verification failed; retain remaining copies.")


def _validate_entry(entry, expected, phase):
    """Validate one recovery record completely before any recovery writes.

    Parameters
    ----------
    entry : dict
        Journal target, hashes, and temporary paths.
    expected : dict
        Accepted hash sets keyed by canonical installed paths.
    phase : str
        Prepared or committed transaction phase.
    """
    target = _validate_paths(entry, expected)
    current = file_sha256(target)
    if phase == "committed" and current != entry["after"]:
        raise Refusal("Committed artifact changed; automatic recovery refused.")
    if current not in (entry["before"], entry["after"]):
        raise Refusal("Browser changed after interruption; automatic recovery refused.")
    required = entry["after"] if phase == "committed" else entry["before"]
    if current != required and file_sha256(Path(entry["rollback"])) != entry["before"]:
        raise Refusal("Recovery copy failed verification.")


def _validate_paths(entry, expected):
    """Constrain journal paths to reviewed targets and sibling staging files.

    Parameters
    ----------
    entry : dict
        Artifact recovery record.
    expected : dict
        Reviewed canonical paths and accepted hashes.

    Returns
    -------
    Path
        Validated canonical installed path.
    """
    target = Path(entry["path"]).resolve()
    if str(target) not in expected or not {entry["before"], entry["after"]} <= expected[str(target)]:
        raise Refusal("Recovery journal does not match reviewed artifacts.")
    for field in ("new", "rollback"):
        temporary = Path(entry[field]).resolve()
        if temporary.parent != target.parent or not temporary.name.startswith(f".{target.name}."):
            raise Refusal("Recovery copy escapes the reviewed artifact directory.")
    return target
