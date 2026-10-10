"""Render verified artifact recipes without modifying installed files."""

from pathlib import Path

from .common.exceptions import Refusal
from .common.models import Artifact, Installation
from .common.utils import backup_directory, find_all, preserve_original, read_binary, sha256


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
            if not callable(recipe["builder"]):
                raise Refusal("Patch builder must be an explicitly registered callable.")
            result = recipe["builder"](result)
        sites.extend(recipe.get("sites", []))
    validate_replacement_sites(result, artifact, sites)
    for site in sorted(sites, key=lambda s: s["offset"], reverse=True):
        before = bytes.fromhex(site["before"])
        result = result[: site["offset"]] + bytes.fromhex(site["after"]) + result[site["offset"] + len(before) :]
    return result


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
        if not before or offset < 0 or find_all(result, before) != [offset]:
            raise Refusal(f"Patch requires a unique original match in {artifact.path.name}.")
        end = offset + len(before)
        if any(offset < other_end and other_start < end for other_start, other_end in ranges):
            raise Refusal("Patch sites overlap.")
        ranges.append((offset, end))


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
