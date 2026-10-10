"""Shared hashing, file reading, backup paths, and process guards."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from .exceptions import Refusal
from .models import Artifact, Installation


def _user_home() -> Path | None:
    """Safely obtain the user home directory if resolvable.

    Returns
    -------
    Path or None
        User home directory, or None if it cannot be determined.
    """
    try:
        return Path.home()
    except (RuntimeError, OSError):
        return None


def _expand_percent_token(match: re.Match) -> str:
    """Expand one percent-delimited variable token to its resolved value.

    Parameters
    ----------
    match : re.Match
        Regular expression match object for a variable token.

    Returns
    -------
    str
        Resolved environment value or fallback path.
    """
    var = match.group(1)
    val = os.environ.get(var)
    if val is None:
        val = os.environ.get(var.upper())
    if val is None:
        val = os.environ.get(var.lower())
    if val is not None:
        return val
    if var.upper() == "LOCALAPPDATA":
        home = _user_home()
        return str(home / "AppData" / "Local") if home else match.group(0)
    if var.upper() in ("PROGRAMFILES", "PROGRAMW6432"):
        drive = os.environ.get("SystemDrive", "C:")
        return f"{drive}\\Program Files"
    if var.upper() == "USERPROFILE":
        home = _user_home()
        return str(home) if home else match.group(0)
    return match.group(0)


def resolve_manifest_path(path: str | Path) -> Path:
    """Expand environment variables and user home directory in a path.

    Parameters
    ----------
    path : str or Path
        Filesystem path potentially containing environment variables or home references.

    Returns
    -------
    Path
        Resolved filesystem path.
    """
    raw = str(path)
    expanded = re.sub(r"%([^%]+)%", _expand_percent_token, raw)
    expanded = os.path.expandvars(expanded)
    try:
        return Path(expanded).expanduser()
    except (RuntimeError, OSError):
        return Path(expanded)


def component_versions(root: Path) -> set[str]:
    """Inspect a component directory for numeric version subdirectories.

    Parameters
    ----------
    root : Path
        Settings component directory.

    Returns
    -------
    set of str
        Discovered version directory names.
    """
    try:
        return {p.name for p in root.iterdir() if p.is_dir() and p.name[:1].isdigit()}
    except OSError as exc:
        raise Refusal(f"Could not validate active settings component: {exc}") from exc


def validate_component(component: dict | None) -> None:
    """Refuse unknown or ambiguous active settings component versions.

    Parameters
    ----------
    component : dict or None
        Expected settings directory and version, or None to skip this guard.
    """
    if not component:
        return
    root = resolve_manifest_path(component["directory"])
    if component_versions(root) != {component["version"]}:
        raise Refusal("Settings component version changed or is ambiguous; review the new version explicitly.")


def validate_states(files: dict[str, Artifact], states: dict, declared_count: int) -> None:
    """Validate complete and unambiguous installation hash states.

    Parameters
    ----------
    files : dict[str, Artifact]
        Declared installed files and their original hashes.
    states : dict
        Parsed target combinations and file hashes.
    declared_count : int
        State count before combinations were normalized.
    """
    if len(states) != declared_count or () not in states:
        raise Refusal("Manifest has duplicate states or no original state.")
    if states[()] != {name: f.original_sha256 for name, f in files.items()}:
        raise Refusal("Original file hashes disagree with the original state.")
    expected_files = set(files)
    if any(set(hashes) != expected_files for hashes in states.values()):
        raise Refusal("Every installation state must identify every file.")


def parse_previous_states(entries: list[dict], current: dict) -> tuple[tuple[tuple[str, ...], dict[str, str]], ...]:
    """Accept explicitly verified prior revisions as migration inputs only.

    Parameters
    ----------
    entries : list[dict]
        Prior target combinations and complete file hashes.
    current : dict
        Current output states used to validate target and file identities.

    Returns
    -------
    tuple
        Prior input states; rendering always uses the current output allowlist.
    """
    previous = []
    seen = list(current.values())
    for entry in entries:
        targets = tuple(sorted(entry["targets"]))
        hashes = entry["sha256"]
        if targets not in current or set(hashes) != set(current[targets]):
            raise Refusal("Prior revision must identify a supported target combination and every file.")
        if hashes in seen:
            raise Refusal("Prior revision duplicates an accepted file state.")
        seen.append(hashes)
        previous.append((targets, hashes))
    return tuple(previous)


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
        home_app_data = _user_home()
        if home_app_data and (home_app_data / "AppData" / "Local").is_dir():
            local_app_data = str(home_app_data / "AppData" / "Local")
        else:
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
    if hashes != build.states[current]:
        print("Prior patch revision; enable the active targets to upgrade.")
    print("Available: " + ", ".join(build.targets))
    return 0
