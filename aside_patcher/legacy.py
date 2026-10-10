"""Validate, apply, and restore supported legacy single-file patches."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import Refusal, atomic_replace, backup_directory, file_sha256, read_binary, sha256
from .processes import require_app_stopped


@dataclass(frozen=True)
class PatchSite:
    """Describe one exact byte replacement at a verified offset."""

    offset: int
    before: bytes
    after: bytes


@dataclass(frozen=True)
class Patch:
    """Group verified replacement sites under one selectable target."""

    target: str
    description: str
    sites: tuple[PatchSite, ...]


@dataclass(frozen=True)
class Build:
    """Describe a supported legacy single-file build and its known hashes."""

    version: str
    binary_path: Path
    original_sha256: str
    states: dict[str, tuple[str, ...]]
    patches: dict[str, Patch]

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "Build":
        """Parse a manifest entry into a supported build definition.

        Parameters
        ----------
        raw : dict
            Decoded manifest entry.

        Returns
        -------
        Build or Installation
            Parsed build definition.
        """
        patches: dict[str, Patch] = {}
        for target, value in raw.get("patches", {}).items():
            if value is None:
                continue
            patches[target] = Patch(
                target=target,
                description=value["description"],
                sites=tuple(
                    PatchSite(
                        int(site["offset"]),
                        bytes.fromhex(site["before"]),
                        bytes.fromhex(site["after"]),
                    )
                    for site in value["sites"]
                ),
            )

        states = {state["sha256"].lower(): tuple(state.get("targets", [])) for state in raw["states"]}
        return cls(
            version=raw["version"],
            binary_path=Path(raw["binary_path"]),
            original_sha256=raw["original_sha256"].lower(),
            states=states,
            patches=patches,
        )


def state_for(data: bytes, build: Build) -> tuple[str, ...] | None:
    """Look up the targets associated with an exact binary hash.

    Parameters
    ----------
    data : bytes
        Bytes to inspect or write.
    build : Build
        Supported build definition and hash allowlist.

    Returns
    -------
    tuple of str or None
        Active targets, or None for an unsupported hash.
    """
    return build.states.get(sha256(data))


def _find_all(data: bytes, needle: bytes) -> list[int]:
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


def apply_to_bytes(data: bytes, build: Build, targets: list[str]) -> bytes:
    """Apply verified legacy patches and validate the resulting hash.

    Parameters
    ----------
    data : bytes
        Bytes to inspect or write.
    build : Build
        Supported build definition and hash allowlist.
    targets : sequence of str
        Selected patch target names.

    Returns
    -------
    bytes
        Patched bytes with a verified resulting hash.
    """
    current_sha = sha256(data)
    current_state = build.states.get(current_sha)
    if current_state is None:
        raise Refusal(f"Unsupported binary hash: {current_sha}")

    selected = tuple(dict.fromkeys(targets))
    if not selected:
        raise Refusal("Select at least one target.")

    candidate = bytearray(data)
    active = set(current_state)
    modified_ranges: list[tuple[int, int]] = []
    for target in selected:
        patch = build.patches.get(target)
        if patch is None:
            raise Refusal(f"No verified patch is configured for target '{target}'.")
        if target in active:
            continue

        validate_legacy_sites(data, patch, modified_ranges)
        for site in patch.sites:
            candidate[site.offset : site.offset + len(site.before)] = site.after
        active.add(target)

    result = bytes(candidate)
    expected_state = build.states.get(sha256(result))
    if expected_state is None or set(expected_state) != active:
        raise Refusal("Result hash is not listed as a verified state; no file was changed.")
    return result


def ensure_immutable_backup(original: bytes, build: Build, root: Path) -> Path:
    """Preserve a verified original without overwriting an existing backup.

    Parameters
    ----------
    original : bytes
        Verified vendor-original file contents.
    build : Build
        Supported build definition and hash allowlist.
    root : Path
        Directory containing immutable original backups.

    Returns
    -------
    Path
        Verified original backup path.
    """
    original_hash = sha256(original)
    if original_hash != build.original_sha256:
        raise Refusal("Refusing to back up an unsupported original binary.")
    try:
        root.mkdir(parents=True, exist_ok=True)
        backup = root / f"{original_hash}.dll"
        if backup.exists():
            if file_sha256(backup) != original_hash:
                raise Refusal(f"Existing backup is corrupt and will not be overwritten: {backup}")
            return backup

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(backup, flags, 0o444)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(original)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            backup.unlink(missing_ok=True)
            raise
        return backup
    except OSError as exc:
        raise Refusal(f"Could not preserve the original backup: {exc}") from exc


def restore_bytes(current: bytes, backup: bytes, build: Build) -> bytes:
    """Return vendor bytes only for a known installed binary state.

    Parameters
    ----------
    current : bytes
        Currently installed binary contents.
    backup : bytes
        Contents of the preserved vendor original.
    build : Build
        Supported build definition and hash allowlist.

    Returns
    -------
    bytes
        Verified original contents, or unchanged original input.
    """
    current_state = build.states.get(sha256(current))
    if current_state is None:
        raise Refusal(f"Refusing to restore an unknown installed hash: {sha256(current)}")
    if not current_state:
        return current
    if sha256(backup) != build.original_sha256:
        raise Refusal("The original backup is missing or does not match its recorded SHA-256.")
    return backup


def validate_legacy_sites(data: bytes, patch: Patch, modified_ranges: list[tuple[int, int]]) -> None:
    """Validate legacy byte sites and track nonoverlapping replacement ranges.

    Parameters
    ----------
    data : bytes
        Mutable DLL contents, or the binary bytes under validation.
    patch : Patch
        Legacy target definition and its exact replacement sites.
    modified_ranges : list[tuple[int, int]]
        Previously validated byte spans; accepted spans are appended here.
    """
    if not patch.sites:
        raise Refusal(f"Patch '{patch.target}' has no sites.")
    for site in patch.sites:
        validate_legacy_match(data, patch.target, site)
        end = site.offset + len(site.before)
        if any(site.offset < other_end and other_start < end for other_start, other_end in modified_ranges):
            raise Refusal(f"Patch '{patch.target}' overlaps another patch site.")
        modified_ranges.append((site.offset, end))


def validate_legacy_match(data: bytes, target: str, site: PatchSite) -> None:
    """Require equal-length replacements and one unambiguous original match.

    Parameters
    ----------
    data : bytes
        Mutable DLL contents, or the binary bytes under validation.
    target : str
        Selected legacy target name for refusal messages.
    site : PatchSite
        Original byte pattern, replacement bytes, and verified offset.
    """
    if len(site.before) != len(site.after) or not site.before or site.before == site.after:
        raise Refusal(f"Patch '{target}' has an invalid replacement length or unchanged bytes.")
    before_offsets = _find_all(data, site.before)
    after_offsets = _find_all(data, site.after)
    if before_offsets != [site.offset] or after_offsets:
        raise Refusal(
            f"Patch '{target}' expected one exact byte match at 0x{site.offset:X}; "
            f"found {len(before_offsets)} original and {len(after_offsets)} patched matches."
        )


def _state_label(targets: tuple[str, ...] | None) -> str:
    """Format a legacy patch state for terminal output.

    Parameters
    ----------
    targets : tuple of str or None
        Active targets, or None for an unknown state.

    Returns
    -------
    str
        Human-readable state label.
    """
    if targets is None:
        return "unknown / unsupported"
    if not targets:
        return "original"
    return "patched: " + ", ".join(targets)


def run_status(build: Build, path: Path) -> int:
    """Print the installed legacy binary hash and available patches.

    Parameters
    ----------
    build : Build
        Supported build definition and hash allowlist.
    path : Path
        File to read or replace.

    Returns
    -------
    int
        Exit code: zero on success, or two for an unsupported state or refusal.
    """
    digest = file_sha256(path)
    state = build.states.get(digest)
    print(f"Aside build: {build.version}")
    print(f"Binary: {path}")
    print(f"SHA-256: {digest}")
    print(f"State: {_state_label(state)}")
    for target in ("chats", "ask-aside", "chats-toggle"):
        if target in build.patches:
            print(f"{target}: verified patch available")
        else:
            print(f"{target}: no verified patch configured; apply is disabled")
    return 0 if state is not None else 2


def run_apply(build: Build, path: Path, targets: list[str]) -> int:
    """Back up and apply selected patches to a legacy binary.

    Parameters
    ----------
    build : Build
        Supported build definition and hash allowlist.
    path : Path
        File to read or replace.
    targets : sequence of str
        Selected patch target names.

    Returns
    -------
    int
        Exit code: zero on success, or two for an unsupported state or refusal.
    """
    require_app_stopped()
    original = read_binary(path)
    result = apply_to_bytes(original, build, targets)
    if result == original:
        print("Requested target(s) are already applied.")
        return 0
    backup_root = backup_directory()
    if state_for(original, build):
        backup_path = backup_root / f"{build.original_sha256}.dll"
        ensure_immutable_backup(read_binary(backup_path), build, backup_root)
    else:
        ensure_immutable_backup(original, build, backup_root)
    require_app_stopped()
    atomic_replace(path, result)
    print(f"Applied {', '.join(dict.fromkeys(targets))} to {path}")
    print(f"SHA-256: {file_sha256(path)}")
    return 0


def run_restore(build: Build, path: Path) -> int:
    """Restore a known legacy binary from its verified original backup.

    Parameters
    ----------
    build : Build
        Supported build definition and hash allowlist.
    path : Path
        File to read or replace.

    Returns
    -------
    int
        Exit code: zero on success, or two for an unsupported state or refusal.
    """
    require_app_stopped()
    current = read_binary(path)
    state = state_for(current, build)
    if state is None:
        raise Refusal(f"Refusing to restore unknown installed hash: {file_sha256(path)}")
    if not state:
        print("Aside.dll already matches the supported original build.")
        return 0

    backup = backup_directory() / f"{build.original_sha256}.dll"
    original = read_binary(backup)
    restored = restore_bytes(current, original, build)
    require_app_stopped()
    atomic_replace(path, restored)
    print(f"Restored the original {build.version} binary from {backup}")
    print(f"SHA-256: {file_sha256(path)}")
    return 0
