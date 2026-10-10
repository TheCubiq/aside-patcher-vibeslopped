"""Shared hashing, file reading, backup paths, and process guards."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from .exceptions import Refusal
from .models import Artifact, Installation


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


def find_all(data: bytes, needle: bytes) -> list[int]:
    """Find every byte match, including overlapping occurrences.

    Parameters
    ----------
    data : bytes
        Bytes to inspect or write.
    needle : bytes
        Nonempty byte pattern to locate.

    Returns
    -------
    list of int
        Matching byte offsets in ascending order.
    """
    offsets: list[int] = []
    start = 0
    while True:
        found = data.find(needle, start)
        if found < 0:
            return offsets
        offsets.append(found)
        start = found + 1


def preserve_original(data: bytes, artifact: Artifact, root: Path) -> Path:
    """Store a verified original in a hash-keyed read-only backup.

    Parameters
    ----------
    data : bytes
        Bytes to inspect or write.
    artifact : Artifact
        File definition with original hash and patch recipes.
    root : Path
        Directory containing immutable original backups.

    Returns
    -------
    Path
        Verified read-only backup path.
    """
    if sha256(data) != artifact.original_sha256:
        raise Refusal(f"Invalid original for {artifact.path.name}.")
    backup = root / f"{artifact.original_sha256}{artifact.path.suffix}"
    created = False
    try:
        root.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            if file_sha256(backup) != artifact.original_sha256:
                raise Refusal(f"Existing backup is corrupt and will not be overwritten: {backup}")
            backup.chmod(0o444)
            return backup
        with backup.open("xb") as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        backup.chmod(0o444)
        return backup
    except OSError as exc:
        if created:
            backup.chmod(0o644)
            backup.unlink(missing_ok=True)
        raise Refusal(f"Could not preserve original {artifact.path.name}: {exc}") from exc


def report_status(build: Installation, hashes: dict[str, str]) -> int:
    """Print file hashes and the uniquely identified installed target state.

    Parameters
    ----------
    build : Installation
        Supported installation definition and complete hash allowlist.
    hashes : dict[str, str]
        Observed hashes for every installed artifact.

    Returns
    -------
    int
        Zero after successfully identifying and printing the installed state.
    """
    print(f"Aside build: {build.version}")
    for name, artifact in build.files.items():
        print(f"{name}: {artifact.path}\nSHA-256: {hashes[name]}")
    current = build.identify(hashes)
    print("State: " + (", ".join(current) if current else "original"))
    print("Available: " + ", ".join(build.targets))
    return 0
