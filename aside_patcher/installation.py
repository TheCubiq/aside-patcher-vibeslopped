"""Validated multi-file mods and rollback of an interrupted replacement."""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from .core import (
    Refusal,
    _find_all,
    backup_directory,
    file_sha256,
    read_binary,
    require_app_stopped,
    sha256,
)


@dataclass(frozen=True)
class Artifact:
    path: Path
    original_sha256: str
    recipes: dict


@dataclass(frozen=True)
class Installation:
    version: str
    files: dict[str, Artifact]
    states: dict[tuple[str, ...], dict[str, str]]
    conflicts: tuple[tuple[str, ...], ...]
    targets: tuple[str, ...]
    component: dict | None = None

    @classmethod
    def from_mapping(cls, raw):
        files = {
            name: Artifact(Path(f["path"]), f["original_sha256"], f.get("patches", {}))
            for name, f in raw["files"].items()
        }
        states = {tuple(sorted(s["targets"])): s["sha256"] for s in raw["states"]}
        if len(states) != len(raw["states"]) or () not in states:
            raise Refusal("Manifest has duplicate states or no original state.")
        if states[()] != {name: f.original_sha256 for name, f in files.items()}:
            raise Refusal("Original file hashes disagree with the original state.")
        for hashes in states.values():
            if set(hashes) != set(files):
                raise Refusal("Every installation state must identify every file.")
        return cls(
            raw["version"],
            files,
            states,
            tuple(tuple(c) for c in raw.get("conflicts", [])),
            tuple(raw["targets"]),
            raw.get("component"),
        )

    def identify(self, hashes):
        matches = [
            state for state, expected in self.states.items() if expected == hashes
        ]
        if len(matches) != 1:
            known = all(
                any(s[name] == digest for s in self.states.values())
                for name, digest in hashes.items()
            )
            label = (
                "inconsistent file combination"
                if known
                else "unknown / unsupported file hash"
            )
            raise Refusal(f"Installed files have an {label}; no files were changed.")
        return matches[0]

    def select(self, current, selected):
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
    if sha256(data) != artifact.original_sha256:
        raise Refusal(f"Invalid original for {artifact.path.name}.")
    backup = root / f"{artifact.original_sha256}{artifact.path.suffix}"
    created = False
    try:
        root.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            if file_sha256(backup) != artifact.original_sha256:
                raise Refusal(
                    f"Existing backup is corrupt and will not be overwritten: {backup}"
                )
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
        raise Refusal(
            f"Could not preserve original {artifact.path.name}: {exc}"
        ) from exc


def render(original: bytes, artifact: Artifact, targets: tuple[str, ...]) -> bytes:
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
            from .native_toggle import build_native_toggle

            result = build_native_toggle(result)
        sites.extend(recipe.get("sites", []))
    ranges = []
    for site in sites:
        offset = site["offset"]
        before = bytes.fromhex(site["before"])
        if not before or offset < 0 or _find_all(result, before) != [offset]:
            raise Refusal(
                f"Patch requires a unique original match in {artifact.path.name}."
            )
        end = offset + len(before)
        if any(offset < b and a < end for a, b in ranges):
            raise Refusal("Patch sites overlap.")
        ranges.append((offset, end))
    for site in sorted(sites, key=lambda s: s["offset"], reverse=True):
        before = bytes.fromhex(site["before"])
        result = (
            result[: site["offset"]]
            + bytes.fromhex(site["after"])
            + result[site["offset"] + len(before) :]
        )
    return result


def _stage(path: Path, data: bytes, label: str) -> Path:
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
    """Prepare forward and rollback files before making any replacement."""
    staged, rollback, completed = {}, {}, []
    rollback_failed = False
    try:
        for path, (before, after) in changes.items():
            staged[path] = _stage(path, after, "new")
            rollback[path] = _stage(path, before, "rollback")
        require_app_stopped()
        for path, (before, after) in changes.items():
            if file_sha256(path) != sha256(before):
                raise Refusal(f"File changed while staging: {path}")
        for path, (_, after) in changes.items():
            os.replace(staged[path], path)
            completed.append(path)
            if file_sha256(path) != sha256(after):
                raise Refusal(f"Replacement verification failed: {path}")
    except (OSError, Refusal) as exc:
        errors = []
        for path in reversed(completed):
            try:
                os.replace(rollback[path], path)
                if file_sha256(path) != sha256(changes[path][0]):
                    raise Refusal("Rollback hash mismatch.") from exc
            except (OSError, Refusal) as rollback_exc:
                errors.append(
                    f"{path}: {rollback_exc}; recovery copy: {rollback[path]}"
                )
        rollback_failed = bool(errors)
        detail = (
            "; rollback failed: " + "; ".join(errors)
            if errors
            else "; pre-operation files preserved"
        )
        raise Refusal(f"Installation failed: {exc}{detail}") from exc
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)
        if not rollback_failed:
            for path in rollback.values():
                path.unlink(missing_ok=True)


def run_installation(build: Installation, command: str, targets=None) -> int:
    if command != "status":
        require_app_stopped()
    if build.component:
        component_root = Path(build.component["directory"])
        try:
            versions = {
                p.name
                for p in component_root.iterdir()
                if p.is_dir() and p.name[:1].isdigit()
            }
        except OSError as exc:
            raise Refusal(
                f"Could not validate active settings component: {exc}"
            ) from exc
        if versions != {build.component["version"]}:
            raise Refusal(
                "Settings component version changed or is ambiguous; "
                "review the new version explicitly."
            )
    hashes = {name: file_sha256(f.path) for name, f in build.files.items()}
    if command == "status":
        print(f"Aside build: {build.version}")
        for name, artifact in build.files.items():
            print(f"{name}: {artifact.path}\nSHA-256: {hashes[name]}")
    current = build.identify(hashes)
    if command == "status":
        print("State: " + (", ".join(current) if current else "original"))
        print("Available: " + ", ".join(build.targets))
        return 0
    desired = () if command == "restore" else build.select(current, targets or [])
    if desired == current:
        print("Requested state is already installed.")
        return 0
    root = backup_directory()
    changes = {}
    for name, artifact in build.files.items():
        before = read_binary(artifact.path)
        if sha256(before) != hashes[name]:
            raise Refusal(f"File changed during validation: {artifact.path}")
        backup = root / f"{artifact.original_sha256}{artifact.path.suffix}"
        original = (
            before if hashes[name] == artifact.original_sha256 else read_binary(backup)
        )
        preserve_original(original, artifact, root)
        after = render(original, artifact, desired)
        if sha256(after) != build.states[desired][name]:
            raise Refusal(f"Result hash is not verified for {artifact.path.name}.")
        if before != after:
            changes[artifact.path] = (before, after)
    replace_transaction(changes)
    print("Installed: " + (", ".join(desired) if desired else "vendor originals"))
    return 0
