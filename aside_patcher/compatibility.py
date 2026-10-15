"""Signed data-only compatibility packages and bounded original-image deltas."""

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .common.exceptions import Refusal
from .common.models import Artifact, Installation, PatchDefinition
from .common.utils import sha256, validate_states
from .windows import installation_lock

ENGINE_VERSION = 1
PUBLIC_KEY = Path(__file__).resolve().parents[1] / "compatibility" / "public-key.pem"
CATALOG_URL = "https://github.com/TheCubiq/aside-patcher-vibeslopped/releases/latest/download/catalog.json"
MAX_DOWNLOAD = 32 * 1024 * 1024


def _public_key():
    """Read the desktop-pinned public key, falling back to the developer checkout.

    Returns
    -------
    bytes or None
        Public verification key, or None before owner setup.
    """
    pinned = os.environ.get("ASIDE_PATCHER_PUBLIC_KEY")
    return pinned.encode() if pinned else (PUBLIC_KEY.read_bytes() if PUBLIC_KEY.exists() else None)


def canonical(value: dict) -> bytes:
    """Encode a compatibility document for signing without platform whitespace.

    Parameters
    ----------
    value : dict
        JSON document to authenticate.

    Returns
    -------
    bytes
        Canonical UTF-8 encoding.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def verify_document(data: bytes, public_key: bytes, *, allow_expired: bool = False) -> dict:
    """Verify an Ed25519 envelope before interpreting compatibility instructions.

    Parameters
    ----------
    data : bytes
        Signed JSON envelope.
    public_key : bytes
        Pinned PEM public key.
    allow_expired : bool
        Permit already cached support offline while still verifying its signature.

    Returns
    -------
    dict
        Authenticated schema-compatible payload.
    """
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key  # pylint: disable=import-outside-toplevel
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # pylint: disable=import-outside-toplevel
        from cryptography.exceptions import InvalidSignature  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        raise Refusal("Install the catalog extra to verify compatibility updates.") from exc
    try:
        envelope = json.loads(data)
        if not isinstance(envelope, dict) or not isinstance(envelope.get("signed"), dict):
            raise ValueError("Expected an object envelope and payload")
        key = load_pem_public_key(public_key)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Expected an Ed25519 public key")
        key.verify(base64.b64decode(envelope["signature"], validate=True), canonical(envelope["signed"]))
        payload = envelope["signed"]
        _validate_header(payload, allow_expired)
        return payload
    except (InvalidSignature, ValueError, TypeError, KeyError) as exc:
        raise Refusal("Invalid signature or unsupported compatibility document.") from exc


def _validate_header(payload, allow_expired):
    """Require supported integer versions and a timezone-aware expiry.

    Parameters
    ----------
    payload : dict
        Authenticated document header.
    allow_expired : bool
        Allow verified cached documents past their expiry.
    """
    for name, maximum in (("schema_version", 1), ("minimum_engine_version", ENGINE_VERSION)):
        value = payload[name]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
            raise ValueError("Unsupported compatibility schema or engine version")
    expiry = payload.get("expires_at")
    if expiry is not None:
        expires_at = datetime.fromisoformat(expiry)
        if expires_at.utcoffset() is None:
            raise ValueError("Compatibility expiry needs a timezone")
        if not allow_expired and expires_at <= datetime.now(timezone.utc):
            raise ValueError("Compatibility catalog expired")


def apply_delta(original: bytes, recipe: dict) -> bytes:
    """Apply a data-only exact-input delta; no executable downloader scripts.

    Parameters
    ----------
    original : bytes
        Verified vendor image.
    recipe : dict
        Original/output hashes, output size, and nonoverlapping changed segments.

    Returns
    -------
    bytes
        Fully hash-verified reconstructed image.
    """
    if sha256(original) != recipe["original_sha256"]:
        raise Refusal("Compatibility delta requires the exact vendor original.")
    size = recipe["size"]
    if not isinstance(size, int) or not 0 < size <= len(original) + MAX_DOWNLOAD:
        raise Refusal("Invalid compatibility delta output size.")
    result = bytearray(original[:size])
    result.extend(b"\0" * (size - len(result)))
    previous = 0
    for segment in recipe["segments"]:
        offset = segment["offset"]
        data = _segment_data(segment, recipe.get("blobs", {}), original)
        if offset < previous or offset + len(data) > size:
            raise Refusal("Overlapping or out-of-range compatibility segment.")
        result[offset : offset + len(data)] = data
        previous = offset + len(data)
    rendered = bytes(result)
    if sha256(rendered) != recipe["sha256"]:
        raise Refusal("Compatibility delta output hash mismatch.")
    return rendered


def _segment_data(segment, blobs, original):
    """Read inline or shared data without trusting a mismatched content identity.

    Parameters
    ----------
    segment : dict
        Inline base64 data or a content-addressed reference.
    blobs : dict
        Authenticated shared base64 payloads.
    original : bytes
        Exact verified vendor input for bounded copy instructions.

    Returns
    -------
    bytes
        Verified replacement segment bytes.
    """
    if sum(name in segment for name in ("data", "data_ref", "copy_offset")) != 1:
        raise Refusal("Compatibility segment must declare exactly one data source.")
    if "copy_offset" in segment:
        return _copied_data(segment, original)
    if "data_ref" not in segment:
        return base64.b64decode(segment["data"], validate=True)
    digest = segment["data_ref"]
    data = base64.b64decode(blobs[digest], validate=True)
    if sha256(data) != digest:
        raise Refusal("Shared compatibility payload digest mismatch.")
    return data


def _copied_data(segment, original):
    """Copy a bounded vendor byte range instead of distributing vendor tables.

    Parameters
    ----------
    segment : dict
        Source offset and byte count.
    original : bytes
        Hash-verified original browser artifact.

    Returns
    -------
    bytes
        Requested immutable input span.
    """
    offset, size = segment["copy_offset"], segment["size"]
    if not isinstance(offset, int) or not isinstance(size, int) or offset < 0 or size <= 0 or offset + size > len(original):
        raise Refusal("Compatibility copy is outside the verified original.")
    return original[offset : offset + size]


def build_from_package(package: dict, version: str) -> Installation:
    """Parse authenticated compatibility data into a reviewed engine definition.

    Parameters
    ----------
    package : dict
        Already authenticated data-only package.
    version : str
        Exact browser version.

    Returns
    -------
    Installation
        Installation whose combination deltas remain input/output hash guarded.
    """
    candidates = [build for build in package["builds"] if build["version"] == version]
    if len(candidates) != 1:
        raise Refusal("No unique approved compatibility adapter for this browser.")
    raw = candidates[0]
    patches = _package_patches(package, version)
    files = {name: Artifact(Path(item["path"]), item["original_sha256"], {}) for name, item in raw["files"].items()}
    if not files or not files.keys() <= {"dll", "appearance"}:
        raise Refusal("Unknown package artifact type.")
    states = {tuple(sorted(state["targets"])): state["sha256"] for state in raw["states"]}
    validate_states(files, states, len(raw["states"]))
    _validate_targets(states, _supported_ids(package))
    payloads = _state_payloads(raw, package.get("blobs", {}))
    return Installation(
        version,
        files,
        states,
        _patch_conflicts(patches),
        raw.get("component"),
        patches,
        tuple((tuple(item["targets"]), item["sha256"]) for item in raw.get("previous_states", [])),
        payloads,
        {patch["id"]: patch for patch in package["patches"]},
    )


def _state_payloads(raw, blobs):
    """Bind shared authenticated bytes to each complete combination recipe.

    Parameters
    ----------
    raw : dict
        Authenticated browser build.
    blobs : dict
        Shared content-addressed payloads.

    Returns
    -------
    dict
        Combination recipes retaining one shared payload table.
    """
    return {
        tuple(sorted(state["targets"])): {name: dict(recipe, blobs=blobs) for name, recipe in state["payloads"].items()}
        for state in raw["states"]
    }


def _patch_conflicts(patches):
    """Collect declared mutually exclusive patch pairs.

    Parameters
    ----------
    patches : dict
        Reviewed patch definitions.

    Returns
    -------
    tuple
        Conflict pairs for selection validation.
    """
    return tuple((patch.patch_id, conflict) for patch in patches.values() for conflict in patch.conflicts)


def _supported_ids(package):
    """Collect only patch IDs with approved support.

    Parameters
    ----------
    package : dict
        Authenticated availability metadata.

    Returns
    -------
    set
        Patch IDs allowed in advertised combinations.
    """
    return {patch["id"] for patch in package["patches"] if patch["status"] == "supported"}


def _package_patches(package, version):
    """Parse patch capabilities from authenticated package metadata.

    Parameters
    ----------
    package : dict
        Signed package payload.
    version : str
        Exact supported browser version.

    Returns
    -------
    dict
        Supported patch definitions with their artifact dependencies.
    """
    patches = {}
    for patch in package["patches"]:
        if patch["status"] not in ("supported", "awaiting validation", "unsupported", "retired"):
            raise Refusal("Unknown patch compatibility status.")
        if patch["status"] == "retired" and not (patch.get("reason") and patch.get("official_replacement")):
            raise Refusal("Retired patches require reviewed evidence and an official replacement.")
        files = {name: {} for name in patch.get("files", [])}
        if not files.keys() <= {"dll", "appearance"} or (patch["status"] == "supported" and not files):
            raise Refusal("Patch metadata has unknown artifact dependencies.")
        patches[patch["id"]] = PatchDefinition(patch["id"], patch["description"], (version,), tuple(patch["conflicts"]), files)
    return patches


def _validate_targets(states, patches):
    """Reject combinations naming unavailable patches.

    Parameters
    ----------
    states : dict
        Advertised complete target states.
    patches : set
        Authenticated supported patch identifiers.
    """
    if any(not set(targets) <= patches for targets in states):
        raise Refusal("Compatibility combination refers to an unavailable patch.")


def _download(url):
    """Fetch a size-limited document from the patcher's release namespace.

    Parameters
    ----------
    url : str
        HTTPS release asset URL.

    Returns
    -------
    bytes
        Untrusted downloaded bytes awaiting signature verification.
    """
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname != "github.com"
        or not parts.path.startswith("/TheCubiq/aside-patcher-vibeslopped/releases/")
    ):
        raise Refusal("Compatibility downloads must belong to the official patcher release repository.")
    request = Request(url, headers={"User-Agent": "AsidePatcher/0.1"})
    with urlopen(request, timeout=30) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise Refusal("Compatibility document exceeds its download limit.")
    return data


def refresh(cache: Path) -> dict:
    """Refresh approved signed support while preserving the last verified cache.

    Parameters
    ----------
    cache : Path
        Original user's compatibility cache directory.

    Returns
    -------
    dict
        Verified update or configuration/offline status.
    """
    cache.mkdir(parents=True, exist_ok=True)
    with installation_lock(cache):
        return _refresh_locked(cache)


def _refresh_locked(cache):
    """Update one catalog while excluding another writer's sequence check.

    Parameters
    ----------
    cache : Path
        Locked user-scoped compatibility cache.

    Returns
    -------
    dict
        Verified update status.
    """
    public_key = _public_key()
    if not public_key:
        return {"message": "Online support is not configured yet. Bundled verified patches remain available.", "configured": False}
    catalog_bytes = _download(CATALOG_URL)
    catalog = verify_document(catalog_bytes, public_key)
    _validate_catalog(catalog)
    _check_sequence(cache, catalog, public_key)
    packages = []
    versions = set()
    for item in catalog["packages"]:
        data = _download(item["url"])
        if sha256(data) != item["sha256"]:
            raise Refusal("Compatibility package digest mismatch.")
        package = verify_document(data, public_key)
        _validate_package_builds(package, versions)
        packages.append((item["sha256"], data))
    cache.mkdir(parents=True, exist_ok=True)
    for digest, data in packages:
        target = cache / f"{digest}.json"
        if not target.exists():
            with target.open("xb") as stream:
                stream.write(data)
        elif sha256(target.read_bytes()) != digest:
            raise Refusal("Cached compatibility package is corrupt.")
    temporary = cache / "catalog.pending"
    temporary.write_bytes(catalog_bytes)
    temporary.replace(cache / "catalog.json")
    return {"message": "Approved compatibility support refreshed.", "configured": True, "packages": len(packages)}


def _validate_catalog(catalog):
    """Check signed catalog structure before using its sequence or asset paths.

    Parameters
    ----------
    catalog : dict
        Authenticated catalog payload.
    """
    sequence = catalog.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise Refusal("Invalid compatibility catalog sequence.")
    if not catalog.get("expires_at") or not isinstance(catalog.get("packages"), list):
        raise Refusal("Compatibility catalog needs expiry and package entries.")
    seen = set()
    for item in catalog["packages"]:
        digest = _package_digest(item)
        if digest in seen:
            raise Refusal("Duplicate compatibility package entry.")
        seen.add(digest)


def _package_digest(item):
    """Validate one catalog entry before using its digest as a cache filename.

    Parameters
    ----------
    item : dict
        Signed package URL and digest.

    Returns
    -------
    str
        Lowercase SHA-256 digest safe for a cache basename.
    """
    if not isinstance(item, dict) or not isinstance(item.get("url"), str):
        raise Refusal("Invalid compatibility package entry.")
    digest = item.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise Refusal("Invalid compatibility package digest.")
    return digest


def _validate_package_builds(package, versions):
    """Parse every adapter before committing a new catalog to the active cache.

    Parameters
    ----------
    package : dict
        Authenticated package payload.
    versions : set
        Browser versions already claimed by this candidate catalog.
    """
    try:
        builds = package["builds"]
        if not isinstance(builds, list) or not builds:
            raise Refusal("Compatibility package needs browser builds.")
        for build in builds:
            version = build["version"]
            if not isinstance(version, str) or version in versions:
                raise Refusal("Multiple approved packages claim this browser build.")
            build_from_package(package, version)
            versions.add(version)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise Refusal("Malformed compatibility package adapter.") from exc


def _check_sequence(cache, catalog, public_key):
    """Reject rollback and conflicting reuse of an approved sequence.

    Parameters
    ----------
    cache : Path
        Existing verified catalog directory.
    catalog : dict
        Newly authenticated catalog.
    public_key : bytes
        Pinned owner verification key.
    """
    previous_path = cache / "catalog.json"
    if not previous_path.exists():
        return
    previous = verify_document(previous_path.read_bytes(), public_key, allow_expired=True)
    if catalog["sequence"] < previous["sequence"]:
        raise Refusal("Compatibility catalog rollback was refused.")
    if catalog["sequence"] == previous["sequence"] and catalog != previous:
        raise Refusal("Compatibility catalog changed without a new sequence.")


def cached_build(cache: Path, version: str) -> Installation | None:
    """Resolve a browser adapter only through a verified cached catalog.

    Parameters
    ----------
    cache : Path
        User-scoped compatibility cache directory.
    version : str
        Requested browser version.

    Returns
    -------
    Installation or None
        Authenticated adapter, or None when no approved adapter exists.
    """
    key = _public_key()
    if not key or not (cache / "catalog.json").exists():
        return None
    catalog = verify_document((cache / "catalog.json").read_bytes(), key, allow_expired=True)
    _validate_catalog(catalog)
    matches = []
    for item in catalog["packages"]:
        data = (cache / f"{item['sha256']}.json").read_bytes()
        if sha256(data) != item["sha256"]:
            raise Refusal("Cached package digest mismatch.")
        package = verify_document(data, key)
        if any(build["version"] == version for build in package["builds"]):
            matches.append(build_from_package(package, version))
    if len(matches) > 1:
        raise Refusal("Multiple approved packages claim this browser build.")
    return matches[0] if matches else None
