"""Prepare data-only candidates and sign exact tested compatibility artifacts."""

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aside_patcher.common.models import MANIFEST_PATH
from aside_patcher.common.utils import file_sha256, sha256
from aside_patcher.compatibility import MAX_DOWNLOAD, canonical, apply_delta, build_from_package, verify_document
from aside_patcher.manifest import load_build
from aside_patcher.recipes import render

REQUIRED_RUNTIME_CHECKS = ("launch", "patch-behavior", "all-combinations", "restore", "themes", "bookmark-interactions")


def delta(original: bytes, after: bytes) -> dict:
    """Store only changed spans and appended payload bytes."""
    segments = []
    copy_cursor = 0
    for start in range(0, len(after), 4096):
        before_chunk, after_chunk = original[start : start + 4096], after[start : start + 4096]
        if before_chunk == after_chunk:
            continue
        left = 0
        while left < min(len(before_chunk), len(after_chunk)) and before_chunk[left] == after_chunk[left]:
            left += 1
        right = len(after_chunk)
        while right > left and right <= len(before_chunk) and before_chunk[right - 1] == after_chunk[right - 1]:
            right -= 1
        block = after_chunk[left:right]
        segment, copy_cursor = _delta_segment(block, start + left, original, copy_cursor)
        segments.append(segment)
    recipe = {"original_sha256": sha256(original), "sha256": sha256(after), "size": len(after), "segments": segments}
    assert apply_delta(original, recipe) == after
    return recipe


def _delta_segment(block: bytes, offset: int, original: bytes, cursor: int) -> tuple:
    """Reuse verified vendor spans; inline only bytes absent from the original."""
    segment = {"offset": offset}
    position = -1
    if len(block) >= 64:
        position = cursor if original[cursor : cursor + len(block)] == block else original.find(block)
    if position >= 0:
        segment.update(copy_offset=position, size=len(block))
        cursor = position + len(block)
    else:
        segment["data"] = base64.b64encode(block).decode()
    return segment, cursor


def _originals(args: argparse.Namespace, build) -> dict:
    """Read only exact reviewed original artifacts."""
    originals = {"dll": args.dll.read_bytes()}
    if args.settings:
        originals["appearance"] = args.settings.read_bytes()
    for name, artifact in build.files.items():
        if name not in originals:
            continue
        if sha256(originals[name]) != artifact.original_sha256:
            raise ValueError(f"Original {name} hash mismatch")
    return originals


def _candidate_build(args: argparse.Namespace) -> dict:
    """Scope metadata to artifacts whose originals are available."""
    registry = json.loads(MANIFEST_PATH.read_text())
    raw = next(item for item in registry["builds"] if item["version"] == args.version)
    raw = json.loads(json.dumps(raw))
    if not args.settings:
        raw["files"] = {"dll": raw["files"]["dll"]}
        raw.pop("component", None)
        raw["states"] = [state for state in raw["states"] if "chats-toggle" not in state["targets"]]
        raw["previous_states"] = [state for state in raw.get("previous_states", []) if "chats-toggle" not in state["targets"]]
        for state in raw["states"] + raw["previous_states"]:
            state["sha256"] = {"dll": state["sha256"]["dll"]}
    return raw


def _render_states(raw: dict, build, originals: dict) -> None:
    """Verify each complete output before storing any candidate payload."""
    for state in raw["states"]:
        targets = tuple(sorted(state["targets"]))
        state["payloads"] = {}
        for name, artifact in build.files.items():
            if name not in originals:
                continue
            after = render(originals[name], artifact, targets)
            if sha256(after) != state["sha256"][name]:
                raise ValueError("Candidate output is not in the reviewed combination allowlist")
            state["payloads"][name] = delta(originals[name], after)
        print("verified candidate:", ",".join(targets) or "originals", flush=True)


def build_candidate(args: argparse.Namespace) -> None:
    """Render every bundled reviewed combination from verified originals."""
    build = load_build(MANIFEST_PATH, args.version)
    originals = _originals(args, build)
    raw = _candidate_build(args)
    _render_states(raw, build, originals)
    package = {
        "schema_version": 1,
        "minimum_engine_version": 1,
        "builds": [raw],
        "patches": [
            {
                "id": patch.patch_id,
                "description": patch.description,
                "conflicts": patch.conflicts,
                "files": list(patch.files),
                "status": "supported" if args.settings or patch.patch_id != "chats-toggle" else "awaiting validation",
            }
            for patch in build.patches.values()
        ],
    }
    package["blobs"] = _intern_payloads(raw)
    _validate_package(package, args.version, originals)
    data = canonical(package)
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "package.unsigned.json"
    if target.exists() and target.read_bytes() != data:
        raise ValueError("Candidate is immutable; choose a fresh output directory")
    target.write_bytes(data)
    report = {
        "schema_version": 1,
        "candidate_sha256": sha256(data),
        "browser_version": args.version,
        "source_commit": os.environ.get("GITHUB_SHA", "local-uncommitted"),
        "static_passed": True,
        "runtime_passed": False,
        "required_runtime_checks": list(REQUIRED_RUNTIME_CHECKS),
        "combination_count": len(raw["states"]),
    }
    (args.output / "candidate.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _validate_package(package: dict, version: str, originals: dict) -> None:
    """Reconstruct every serialized recipe through the client's actual parser."""
    build = build_from_package(package, version)
    for targets, recipes in build.state_payloads.items():
        for name, recipe in recipes.items():
            if sha256(apply_delta(originals[name], recipe)) != build.states[targets][name]:
                raise ValueError("Serialized candidate differs from the reviewed output")


def _intern_payloads(raw: dict) -> dict:
    """Store identical segment bytes once across complete output combinations."""
    blobs = {}
    for state in raw["states"]:
        for recipe in state["payloads"].values():
            for segment in recipe["segments"]:
                if "data" not in segment:
                    continue
                data = segment.pop("data")
                digest = sha256(base64.b64decode(data, validate=True))
                blobs[digest] = data
                segment["data_ref"] = digest
    return blobs


def keygen(args: argparse.Namespace) -> None:
    """Create an owner signing key outside the checkout; never print its contents."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

    if args.private.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("Signing private keys must live outside the repository")
    args.private.parent.mkdir(parents=True, exist_ok=True)
    key = Ed25519PrivateKey.generate()
    with args.private.open("xb") as stream:
        stream.write(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    args.private.chmod(0o600)
    args.public.parent.mkdir(parents=True, exist_ok=True)
    with args.public.open("xb") as stream:
        stream.write(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    print("Owner key created; private material remains outside Git.")


def _tested_digest(args: argparse.Namespace) -> str:
    """Require complete runtime evidence bound to immutable candidate bytes."""
    package_path = args.candidate / "package.unsigned.json"
    candidate = json.loads((args.candidate / "candidate.json").read_text())
    runtime = json.loads(args.runtime.read_text())
    digest = file_sha256(package_path)
    if digest != candidate["candidate_sha256"] or digest != runtime.get("candidate_sha256"):
        raise ValueError("Runtime report is not bound to this exact candidate")
    required = set(REQUIRED_RUNTIME_CHECKS) | set(candidate["required_runtime_checks"])
    if runtime.get("runtime_passed") is not True or not all(runtime.get("checks", {}).get(name) is True for name in required):
        raise ValueError("Required interactive runtime checks have not passed")
    combinations = runtime.get("combinations", [])
    expected = {tuple(sorted(state["targets"])) for state in json.loads(package_path.read_bytes())["builds"][0]["states"]}
    observed = {tuple(sorted(item["targets"])) for item in combinations if item.get("passed") is True}
    if observed != expected:
        raise ValueError("Runtime report must cover every advertised combination")
    return digest


def sign_candidate(args: argparse.Namespace) -> None:
    """Sign the exact candidate only after complete runtime evidence is present."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PublicFormat

    digest = _tested_digest(args)
    package_path = args.candidate / "package.unsigned.json"
    key = load_pem_private_key(args.private.read_bytes(), password=None)
    public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    if public != args.public.read_bytes():
        raise ValueError("Signing key does not match the application's pinned key")
    args.output.mkdir(parents=True, exist_ok=True)

    def envelope(document: dict) -> bytes:
        return canonical({"signed": document, "signature": base64.b64encode(key.sign(canonical(document))).decode()})

    document = json.loads(package_path.read_bytes())
    package = envelope(document)
    if len(package) > MAX_DOWNLOAD:
        raise ValueError("Signed package exceeds the client download limit")
    package_name = f"compatibility-{digest}.json"
    entry = {
        "sha256": sha256(package),
        "url": f"https://github.com/TheCubiq/aside-patcher-vibeslopped/releases/download/{args.tag}/{package_name}",
        "versions": [build["version"] for build in document["builds"]],
    }
    packages = _catalog_packages(args, entry)
    catalog = {
        "schema_version": 1,
        "minimum_engine_version": 1,
        "sequence": args.sequence,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(),
        "packages": packages,
    }
    (args.output / package_name).write_bytes(package)
    (args.output / "catalog.json").write_bytes(envelope(catalog))
    print("Signed exact tested candidate:", digest)


def _catalog_packages(args: argparse.Namespace, entry: dict) -> list:
    """Preserve approved older builds when publishing support for a new build."""
    if not args.previous_catalog:
        return [entry]
    previous = verify_document(args.previous_catalog.read_bytes(), args.public.read_bytes(), allow_expired=True)
    if args.sequence <= previous["sequence"]:
        raise ValueError("Publication sequence must advance the approved catalog")
    retained = []
    for existing in previous["packages"]:
        versions = set(existing.get("versions", []))
        if not versions:
            raise ValueError("Prior catalog lacks build identities; review its migration before publishing")
        overlap = versions.intersection(entry["versions"])
        if overlap and versions - overlap:
            raise ValueError("Cannot partly replace a multi-build package; publish a complete replacement")
        if not overlap:
            retained.append(existing)
    return retained + [entry]


def main():
    """Expose explicit candidate, owner-key setup, and gated signing commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--version", required=True)
    build.add_argument("--dll", type=Path, required=True)
    build.add_argument("--settings", type=Path)
    build.add_argument("--output", type=Path, required=True)
    setup = commands.add_parser("keygen")
    setup.add_argument("--private", type=Path, required=True)
    setup.add_argument("--public", type=Path, required=True)
    sign = commands.add_parser("sign")
    for name in ("candidate", "runtime", "private", "public", "output"):
        sign.add_argument("--" + name, type=Path, required=True)
    sign.add_argument("--sequence", type=int, required=True)
    sign.add_argument("--tag", required=True)
    sign.add_argument("--previous-catalog", type=Path)
    args = parser.parse_args()
    {"build": build_candidate, "keygen": keygen, "sign": sign_candidate}[args.command](args)


if __name__ == "__main__":
    main()
