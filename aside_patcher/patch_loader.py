"""Load local patch metadata and validate its connection to supported builds."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .common.exceptions import Refusal
from .common.models import Artifact, PatchDefinition
from .common.utils import read_binary


def local_asset(directory: Path, filename: str) -> bytes:
    """Read an asset confined to its owning patch directory.

    Parameters
    ----------
    directory : Path
        Directory containing the patch definition.
    filename : str
        Relative asset filename from patch metadata.

    Returns
    -------
    bytes
        Exact asset contents without text encoding or newline conversion.
    """
    root = directory.resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root):
        raise Refusal(f"Patch asset escapes its directory: {filename}")
    return read_binary(path)


def load_sites(directory: Path, sites: list[dict]) -> list[dict]:
    """Resolve text assets into the shared exact-byte recipe format.

    Parameters
    ----------
    directory : Path
        Owning patch directory.
    sites : list[dict]
        Replacement offsets and hexadecimal patterns or local asset references.

    Returns
    -------
    list[dict]
        Replacement sites containing exact before/after hexadecimal strings.
    """
    result = []
    for site in sites:
        normalized = dict(site)
        for side in ("before", "after"):
            reference = normalized.pop(f"{side}_file", None)
            if reference is not None:
                if side in normalized:
                    raise Refusal("A patch site cannot declare both bytes and an asset for the same side.")
                normalized[side] = local_asset(directory, reference).hex()
        result.append(normalized)
    return result


def load_definition(directory: Path, renderers: dict[str, Callable[[bytes], bytes]] | None = None) -> PatchDefinition:
    """Load one patch package with explicitly supplied Python renderers.

    Parameters
    ----------
    directory : Path
        Package directory containing patch.json and optional byte assets.
    renderers : dict[str, Callable], optional
        File-name-to-renderer mapping supplied by the owning Python package.

    Returns
    -------
    PatchDefinition
        Parsed metadata and local renderers, without inspecting installed files.
    """
    try:
        raw = json.loads((directory / "patch.json").read_text(encoding="utf-8"))
        files = {name: dict(recipe, sites=load_sites(directory, recipe.get("sites", []))) for name, recipe in raw["files"].items()}
        definition = PatchDefinition(
            raw["id"],
            raw["description"],
            tuple(raw["versions"]),
            tuple(raw["conflicts"]),
            files,
            renderers or {},
            raw.get("compatibility", {}),
            raw.get("smoke", {}),
        )
        if directory.name.replace("_", "-") != definition.patch_id:
            raise Refusal("Patch ID must match its package directory name.")
        if not definition.files or not definition.renderers.keys() <= definition.files.keys():
            raise Refusal("Patch renderers must refer to declared files.")
        if not all(callable(renderer) for renderer in definition.renderers.values()):
            raise Refusal("Patch renderers must be callable.")
        return definition
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise Refusal(f"Could not load patch definition in {directory}: {exc}") from exc


def attach_recipes(files: dict[str, Artifact], patches: dict[str, PatchDefinition], states: dict) -> dict[str, Artifact]:
    """Attach local recipes only when their hashes agree with the build allowlist.

    Parameters
    ----------
    files : dict[str, Artifact]
        Installed file locations and verified original hashes.
    patches : dict[str, PatchDefinition]
        Registered patches supporting the manifest version.
    states : dict
        Exact whole-installation hashes for every supported target combination.

    Returns
    -------
    dict[str, Artifact]
        File definitions carrying validated local patch recipes.
    """
    recipes = {name: {} for name in files}
    for patch_id, definition in patches.items():
        for name, recipe in definition.files.items():
            validate_file_recipe(name, recipe, files, states.get((patch_id,), {}))
            attached = dict(recipe)
            if name in definition.renderers:
                attached["builder"] = definition.renderers[name]
            recipes[name][patch_id] = attached
    return {name: Artifact(artifact.path, artifact.original_sha256, recipes[name]) for name, artifact in files.items()}


def validate_file_recipe(name: str, recipe: dict, files: dict[str, Artifact], standalone: dict[str, str]) -> None:
    """Require the declared original and standalone patched hashes to match the manifest.

    Parameters
    ----------
    name : str
        File ID declared by the patch.
    recipe : dict
        Patch-local recipe and verified hashes.
    files : dict[str, Artifact]
        Supported installed artifacts.
    standalone : dict[str, str]
        Allowlisted hashes with this patch enabled alone.
    """
    if name not in files:
        raise Refusal(f"Patch refers to an unknown file: {name}")
    if recipe["original_sha256"] != files[name].original_sha256:
        raise Refusal(f"Patch original hash disagrees with the build: {name}")
    if recipe["patched_sha256"] != standalone.get(name):
        raise Refusal(f"Patch standalone hash has not been verified in the build manifest: {name}")


def patches_for_version(version: str, definitions: tuple[PatchDefinition, ...]) -> dict[str, PatchDefinition]:
    """Select registered patch packages supporting the exact browser version.

    Parameters
    ----------
    version : str
        Verified Aside version from the build manifest.
    definitions : tuple[PatchDefinition, ...]
        Explicitly registered patch definitions.

    Returns
    -------
    dict[str, PatchDefinition]
        Unique supported patches indexed by their command-line ID.
    """
    matches = [definition for definition in definitions if version in definition.versions]
    patches = {definition.patch_id: definition for definition in matches}
    if not patches or len(patches) != len(matches):
        raise Refusal("Build has no registered patches or duplicate patch IDs.")
    for definition in patches.values():
        if not set(definition.conflicts) <= patches.keys():
            raise Refusal(f"Patch declares an unknown conflict: {definition.patch_id}")
    return patches
