"""Parse supported build manifests above the independent patching modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import Refusal
from .legacy import Build
from .installation import Artifact, Installation


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
    files = {name: Artifact(Path(f["path"]), f["original_sha256"], f.get("patches", {})) for name, f in raw["files"].items()}
    states = {tuple(sorted(s["targets"])): s["sha256"] for s in raw["states"]}
    if len(states) != len(raw["states"]) or () not in states:
        raise Refusal("Manifest has duplicate states or no original state.")
    if states[()] != {name: f.original_sha256 for name, f in files.items()}:
        raise Refusal("Original file hashes disagree with the original state.")
    for hashes in states.values():
        if set(hashes) != set(files):
            raise Refusal("Every installation state must identify every file.")
    return Installation(
        raw["version"],
        files,
        states,
        tuple(tuple(c) for c in raw.get("conflicts", [])),
        tuple(raw["targets"]),
        raw.get("component"),
    )


def load_build(manifest_path: Path) -> Build | Installation:
    """Load the single supported build from a JSON manifest.

    Parameters
    ----------
    manifest_path : Path
        JSON manifest containing one supported build.

    Returns
    -------
    Build or Installation
        Validated supported build definition.
    """
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        builds = raw["builds"]
        if len(builds) != 1:
            raise Refusal("Manifest must contain exactly one explicitly supported build.")
        if "files" in builds[0]:
            return installation_from_mapping(builds[0])
        build = Build.from_mapping(builds[0])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise Refusal(f"Could not read patch manifest: {exc}") from exc

    if build.original_sha256 not in build.states:
        raise Refusal("Manifest is invalid: original hash is not a known state.")
    if build.states[build.original_sha256]:
        raise Refusal("Manifest is invalid: original state lists patched targets.")
    return build
