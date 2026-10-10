"""Parse supported build manifests above the independent patching modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common.exceptions import Refusal
from .common.models import Artifact, Installation
from .common.utils import resolve_manifest_path, validate_states
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
    files = {name: Artifact(resolve_manifest_path(f["path"]), f["original_sha256"], {}) for name, f in raw["files"].items()}
    states = {tuple(sorted(s["targets"])): s["sha256"] for s in raw["states"]}
    validate_states(files, states, len(raw["states"]))
    patches = patches_for_version(raw["version"], PATCHES)
    files = attach_recipes(files, patches, states)
    conflicts = tuple((patch.patch_id, other) for patch in patches.values() for other in patch.conflicts)
    return Installation(raw["version"], files, states, conflicts, tuple(patches), raw.get("component"), patches)


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
        builds = raw.get("builds") if isinstance(raw, dict) else None
        if not isinstance(builds, list) or len(builds) != 1:
            raise Refusal("Manifest must contain exactly one explicitly supported build.")
        return installation_from_mapping(builds[0])
    except (OSError, ValueError) as exc:
        raise Refusal(f"Could not read patch manifest: {exc}") from exc
