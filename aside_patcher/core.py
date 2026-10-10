"""Validate legacy patch states and preserve original binary contents."""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path


class Refusal(RuntimeError):
    """An input or state did not meet the patcher's safety requirements."""


def sha256(data: bytes) -> str:
    """Return the lowercase SHA-256 digest of bytes.

    Parameters
    ----------
    data : bytes
        Bytes to inspect or write.

    Returns
    -------
    str
        Lowercase hexadecimal digest.
    """
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    """Hash a file incrementally without loading it into memory.

    Parameters
    ----------
    path : Path
        File to read or replace.

    Returns
    -------
    str
        Lowercase hexadecimal digest.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise Refusal(f"Could not hash {path}: {exc}") from exc
    return digest.hexdigest()


def backup_directory() -> Path:
    """Resolve the backup directory outside the repository.

    Returns
    -------
    Path
        Original-backup directory.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise Refusal("LOCALAPPDATA is unavailable; cannot choose a backup location safely.")
    return Path(local_app_data) / "AsidePatcher" / "backups"


def atomic_replace(path: Path, data: bytes) -> None:
    """Stage bytes beside a file and atomically replace it.

    Parameters
    ----------
    path : Path
        File to read or replace.
    data : bytes
        Bytes to inspect or write.
    """
    try:
        path = path.resolve()
    except OSError as exc:
        raise Refusal(f"Could not resolve target path {path}: {exc}") from exc
    if not path.is_file():
        raise Refusal(f"Target file does not exist: {path}")
    temp_path: Path | None = None
    try:
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with temp_path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copystat(path, temp_path)
        os.replace(temp_path, path)
    except OSError as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise Refusal(f"Could not replace {path}: {exc}") from exc


def read_binary(path: Path) -> bytes:
    """Read a binary file and translate access failures into refusals.

    Parameters
    ----------
    path : Path
        File to read or replace.

    Returns
    -------
    bytes
        Complete file contents.
    """
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Refusal(f"Could not read {path}: {exc}") from exc
