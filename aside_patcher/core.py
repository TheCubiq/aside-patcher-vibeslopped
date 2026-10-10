from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class Refusal(RuntimeError):
    """An input or state did not meet the patcher's safety requirements."""


@dataclass(frozen=True)
class PatchSite:
    offset: int
    before: bytes
    after: bytes


@dataclass(frozen=True)
class Patch:
    target: str
    description: str
    sites: tuple[PatchSite, ...]


@dataclass(frozen=True)
class Build:
    version: str
    binary_path: Path
    original_sha256: str
    states: dict[str, tuple[str, ...]]
    patches: dict[str, Patch]

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "Build":
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

        states = {
            state["sha256"].lower(): tuple(state.get("targets", []))
            for state in raw["states"]
        }
        return cls(
            version=raw["version"],
            binary_path=Path(raw["binary_path"]),
            original_sha256=raw["original_sha256"].lower(),
            states=states,
            patches=patches,
        )


def load_build(manifest_path: Path) -> Build:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        builds = raw["builds"]
        if len(builds) != 1:
            raise Refusal(
                "Manifest must contain exactly one explicitly supported build."
            )
        if "files" in builds[0]:
            from .installation import Installation

            return Installation.from_mapping(builds[0])
        build = Build.from_mapping(builds[0])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise Refusal(f"Could not read patch manifest: {exc}") from exc

    if build.original_sha256 not in build.states:
        raise Refusal("Manifest is invalid: original hash is not a known state.")
    if build.states[build.original_sha256]:
        raise Refusal("Manifest is invalid: original state lists patched targets.")
    return build


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise Refusal(f"Could not hash {path}: {exc}") from exc
    return digest.hexdigest()


def state_for(data: bytes, build: Build) -> tuple[str, ...] | None:
    return build.states.get(sha256(data))


def _find_all(data: bytes, needle: bytes) -> list[int]:
    offsets: list[int] = []
    start = 0
    while True:
        found = data.find(needle, start)
        if found < 0:
            return offsets
        offsets.append(found)
        start = found + 1


def apply_to_bytes(data: bytes, build: Build, targets: list[str]) -> bytes:
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

        if not patch.sites:
            raise Refusal(f"Patch '{target}' has no sites.")
        for site in patch.sites:
            if (
                len(site.before) != len(site.after)
                or not site.before
                or site.before == site.after
            ):
                raise Refusal(
                    f"Patch '{target}' has an invalid replacement length "
                    "or unchanged bytes."
                )
            before_offsets = _find_all(data, site.before)
            after_offsets = _find_all(data, site.after)
            if before_offsets != [site.offset] or after_offsets:
                raise Refusal(
                    f"Patch '{target}' expected one exact byte match "
                    f"at 0x{site.offset:X}; "
                    f"found {len(before_offsets)} original and "
                    f"{len(after_offsets)} patched matches."
                )
            end = site.offset + len(site.before)
            if any(
                site.offset < other_end and other_start < end
                for other_start, other_end in modified_ranges
            ):
                raise Refusal(f"Patch '{target}' overlaps another patch site.")
            modified_ranges.append((site.offset, end))
            candidate[site.offset : end] = site.after
        active.add(target)

    result = bytes(candidate)
    expected_state = build.states.get(sha256(result))
    if expected_state is None or set(expected_state) != active:
        raise Refusal(
            "Result hash is not listed as a verified state; no file was changed."
        )
    return result


def backup_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise Refusal(
            "LOCALAPPDATA is unavailable; cannot choose a backup location safely."
        )
    return Path(local_app_data) / "AsidePatcher" / "backups"


def ensure_immutable_backup(original: bytes, build: Build, root: Path) -> Path:
    original_hash = sha256(original)
    if original_hash != build.original_sha256:
        raise Refusal("Refusing to back up an unsupported original binary.")
    try:
        root.mkdir(parents=True, exist_ok=True)
        backup = root / f"{original_hash}.dll"
        if backup.exists():
            if file_sha256(backup) != original_hash:
                raise Refusal(
                    f"Existing backup is corrupt and will not be overwritten: {backup}"
                )
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


def aside_is_running() -> bool:
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
        raise Refusal(
            "Could not determine whether Aside is running; refusing to modify the DLL."
        )
    return "aside.exe" in result.stdout.casefold()


def require_app_stopped() -> None:
    if aside_is_running():
        raise Refusal("Aside is running. Close it completely, then retry.")


def atomic_replace(path: Path, data: bytes) -> None:
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
    try:
        return path.read_bytes()
    except OSError as exc:
        raise Refusal(f"Could not read {path}: {exc}") from exc


def restore_bytes(current: bytes, backup: bytes, build: Build) -> bytes:
    current_state = build.states.get(sha256(current))
    if current_state is None:
        raise Refusal(
            f"Refusing to restore an unknown installed hash: {sha256(current)}"
        )
    if not current_state:
        return current
    if sha256(backup) != build.original_sha256:
        raise Refusal(
            "The original backup is missing or does not match its recorded SHA-256."
        )
    return backup
