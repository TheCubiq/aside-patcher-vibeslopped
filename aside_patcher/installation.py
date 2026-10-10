"""Validated multi-file mods and rollback of an interrupted replacement."""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from .core import (
    Refusal,
    backup_directory,
    file_sha256,
    read_binary,
    sha256,
)

from .legacy import _find_all
from .native_toggle import build_native_toggle
from .processes import require_app_stopped


@dataclass(frozen=True)
class Artifact:
    """Describe one installed file and its verified patch recipes."""

    path: Path
    original_sha256: str
    recipes: dict


@dataclass(frozen=True)
class Installation:
    """Describe compatible file states and mutually exclusive targets."""

    version: str
    files: dict[str, Artifact]
    states: dict[tuple[str, ...], dict[str, str]]
    conflicts: tuple[tuple[str, ...], ...]
    targets: tuple[str, ...]
    component: dict | None = None

    def identify(self, hashes: dict[str, str]) -> tuple[str, ...]:
        """Identify the unique installation state matching all file hashes.

        Parameters
        ----------
        hashes : dict of str to str
            Observed SHA-256 digest for every artifact.

        Returns
        -------
        tuple of str
            Targets installed across the matching file state.
        """
        matches = [state for state, expected in self.states.items() if expected == hashes]
        if len(matches) != 1:
            known = all(any(s[name] == digest for s in self.states.values()) for name, digest in hashes.items())
            label = "inconsistent file combination" if known else "unknown / unsupported file hash"
            raise Refusal(f"Installed files have an {label}; no files were changed.")
        return matches[0]

    def select(self, current: tuple[str, ...], selected: list[str]) -> tuple[str, ...]:
        """Merge requested targets and replace mutually exclusive active targets.

        Parameters
        ----------
        current : sequence of str
            Currently installed target names.
        selected : sequence of str
            Requested target names; conflicting requests are refused.

        Returns
        -------
        tuple of str
            Sorted targets for a verified desired state.
        """
        requested = set(selected)
        if not requested or not requested <= set(self.targets):
            raise Refusal("Select at least one configured target.")
        active = set(current)
        for group in self.conflicts:
            if len(requested.intersection(group)) > 1:
                raise Refusal(f'Mutually exclusive targets: {", ".join(group)}')
            if requested.intersection(group):
                active.difference_update(group)
        state = tuple(sorted(active | requested))
        if state not in self.states:
            raise Refusal("Requested combination has no verified hashes.")
        return state


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


def render(original: bytes, artifact: Artifact, targets: tuple[str, ...]) -> bytes:
    """Build an artifact from original bytes using selected patch recipes.

    Parameters
    ----------
    original : bytes
        Verified vendor-original file contents.
    artifact : Artifact
        File definition with original hash and patch recipes.
    targets : sequence of str
        Selected patch target names.

    Returns
    -------
    bytes
        Rendered artifact; output hash is checked by the caller.
    """
    if sha256(original) != artifact.original_sha256:
        raise Refusal(f"Original backup hash mismatch for {artifact.path.name}.")
    result = original
    sites = []
    for target in targets:
        recipe = artifact.recipes.get(target)
        if not recipe:
            continue
        if recipe.get("builder"):
            if recipe["builder"] != "chats-toggle-v1":
                raise Refusal("Unknown native patch builder.")
            result = build_native_toggle(result)
        sites.extend(recipe.get("sites", []))
    validate_replacement_sites(result, artifact, sites)
    for site in sorted(sites, key=lambda s: s["offset"], reverse=True):
        before = bytes.fromhex(site["before"])
        result = result[: site["offset"]] + bytes.fromhex(site["after"]) + result[site["offset"] + len(before) :]
    return result


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


def validate_replacement_sites(result: bytes, artifact: Artifact, sites: list[dict]) -> None:
    """Require unique original matches and nonoverlapping replacement ranges.

    Parameters
    ----------
    result : bytes
        Candidate file contents before applying these byte replacements.
    artifact : Artifact
        Installed file definition and original hash.
    sites : list[dict]
        Replacement sites decoded from the manifest.
    """
    ranges = []
    for site in sites:
        offset = site["offset"]
        before = bytes.fromhex(site["before"])
        if not before or offset < 0 or _find_all(result, before) != [offset]:
            raise Refusal(f"Patch requires a unique original match in {artifact.path.name}.")
        end = offset + len(before)
        if any(offset < other_end and other_start < end for other_start, other_end in ranges):
            raise Refusal("Patch sites overlap.")
        ranges.append((offset, end))


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


def validate_component(component: dict | None) -> None:
    """Refuse unknown or ambiguous active settings component versions.

    Parameters
    ----------
    component : dict or None
        Expected settings directory and version, or None to skip this guard.
    """
    if not component:
        return
    component_root = Path(component["directory"])
    try:
        versions = {p.name for p in component_root.iterdir() if p.is_dir() and p.name[:1].isdigit()}
    except OSError as exc:
        raise Refusal(f"Could not validate active settings component: {exc}") from exc
    if versions != {component["version"]}:
        raise Refusal("Settings component version changed or is ambiguous; review the new version explicitly.")


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
