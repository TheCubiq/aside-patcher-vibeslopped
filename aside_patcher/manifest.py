"""Parse supported build manifests above the independent patching modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common.exceptions import Refusal
from .common.models import Artifact, Installation
from .patch_loader import attach_recipes, patches_for_version
from .patches import PATCHES


def installation_from_mapping(raw: dict[str, Any]) -> Installation:
    """Parse a manifest entry into a supported build definition.

    Parameters
    ----------
    raw : dict
        Decoded manifest entry.

    Returns
    -------
    Installation
        Parsed multi-file installation definition.
    """
    files = {name: Artifact(Path(f["path"]), f["original_sha256"], {}) for name, f in raw["files"].items()}
    states = {tuple(sorted(s["targets"])): s["sha256"] for s in raw["states"]}
    validate_states(files, states, len(raw["states"]))
    patches = patches_for_version(raw["version"], PATCHES)
    files = attach_recipes(files, patches, states)
    conflicts = tuple((patch.patch_id, other) for patch in patches.values() for other in patch.conflicts)
    return Installation(raw["version"], files, states, conflicts, tuple(patches), raw.get("component"), patches)


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
    for hashes in states.values():
        if set(hashes) != set(files):
            raise Refusal("Every installation state must identify every file.")


def load_build(manifest_path: Path) -> Installation:
    """Load the single supported build from a JSON manifest.

    Parameters
    ----------
    manifest_path : Path
        JSON manifest containing one supported build.

    Returns
    -------
    Installation
        Validated supported build definition.
    """
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        builds = raw["builds"]
        if len(builds) != 1:
            raise Refusal("Manifest must contain exactly one explicitly supported build.")
        return installation_from_mapping(builds[0])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise Refusal(f"Could not read patch manifest: {exc}") from exc


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
