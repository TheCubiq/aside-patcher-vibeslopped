"""Render verified artifact recipes without modifying installed files."""

from .common.exceptions import Refusal
from .common.models import ORIGINAL_DLL_SHA256, Artifact, Payload
from .common.utils import find_all, sha256
from .native_toggle import emit_observer, emit_preference_bridge, emit_registration, emit_visibility
from .pe import append_schema, append_section, append_unwind_table, install_hooks, parse_layout, resolve_blocks


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


def build_native_toggle(original: bytes) -> bytes:
    """Add the verified native Chats preference and visibility hooks.

    Parameters
    ----------
    original : bytes
        Verified vendor-original DLL contents.

    Returns
    -------
    bytes
        DLL containing the native toggle hooks.
    """
    if sha256(original) != ORIGINAL_DLL_SHA256:
        raise Refusal("Native toggle recipe requires the exact original DLL.")
    layout = parse_layout(original)
    new_rva = (max(section.rva + section.virtual_size for section in layout.sections) + 4095) & ~4095
    assert layout.section_table + (len(layout.sections) + 1) * 40 <= min(section.file_offset for section in layout.sections)
    payload = Payload(new_rva)
    emit_registration(payload)
    emit_observer(payload)
    emit_preference_bridge(payload)
    emit_visibility(payload)
    schema_start, schema_size = append_schema(original, payload)
    data = bytearray(original)
    append_unwind_table(original, data, layout, payload)
    resolve_blocks(payload)
    install_hooks(data, layout, payload, schema_start, schema_size)
    return append_section(data, layout, payload)
