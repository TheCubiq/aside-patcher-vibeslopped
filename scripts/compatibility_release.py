"""Prepare data-only candidates and sign exact tested compatibility artifacts."""

# Direct script imports and optional cryptography/analysis dependencies are deliberate.
# pylint: disable=wrong-import-position,import-outside-toplevel

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
from aside_patcher.compatibility import ENGINE_VERSION, MAX_DOWNLOAD, canonical, apply_delta, build_from_package, verify_document
from aside_patcher.manifest import load_build
from aside_patcher.recipes import render

REQUIRED_RUNTIME_CHECKS = ("launch", "patch-behavior", "all-combinations", "restore")


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
        while left < right <= len(before_chunk) and before_chunk[right - 1] == after_chunk[right - 1]:
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


def reuse_analysis(path: Path, identity: dict) -> bool:
    """Reuse complete immutable output only when its analysis inputs still match."""
    if not path.exists():
        return False
    previous = json.loads(path.read_bytes())
    if previous.get("identity") != identity:
        raise ValueError("Analysis output is immutable; use a fresh directory for changed inputs")
    if any(patch["analysis_passed"] for patch in previous["patches"]):
        if not all((path.parent / name).is_file() for name in ("candidate.json", "package.unsigned.json")):
            raise ValueError("Cached candidate is incomplete; choose a fresh output directory")
        candidate = json.loads((path.parent / "candidate.json").read_bytes())
        if file_sha256(path.parent / "package.unsigned.json") != candidate["candidate_sha256"]:
            raise ValueError("Cached candidate changed; choose a fresh output directory")
    elif not (path.parent / "availability.json").is_file():
        raise ValueError("Cached availability analysis is incomplete; choose a fresh output directory")
    return True


def analyze_candidate(args: argparse.Namespace) -> None:  # pylint: disable=too-many-locals
    """Prepare unknown builds per patch; never require prior registry membership."""
    from aside_patcher.patches import PATCHES
    from scripts.patch_analysis import analyze_patch, render_selection, selections, smoke_selections

    record, original = archived_original(args.archive, args.version)
    _, baseline = archived_original(args.archive, "1.0.928.2")
    identity = {
        "engine_version": ENGINE_VERSION,
        "dll_sha256": sha256(original),
        "baseline_sha256": sha256(baseline),
        "analyzer_sha256": file_sha256(Path(__file__).with_name("patch_analysis.py")),
        "patches": {
            patch.patch_id: file_sha256(
                Path(__file__).resolve().parents[1] / "aside_patcher" / "patches" / patch.patch_id.replace("-", "_") / "patch.json"
            )
            for patch in PATCHES
        },
    }
    identity["generator_sha256"] = file_sha256(Path(__file__))
    identity["bookmark_adapter_index_sha256"] = file_sha256(Path(__file__).resolve().parents[1] / "compatibility/adapters/index.json")
    identity["bookmark_renderer_sha256"] = file_sha256(
        Path(__file__).resolve().parents[1] / "aside_patcher/patches/bookmark_folder_appearance/native.py"
    )
    identity["native_discovery_sha256"] = file_sha256(Path(__file__).with_name("native_discovery.py"))
    identity["native_resources_sha256"] = file_sha256(Path(__file__).with_name("native_resources.py"))
    args.output.mkdir(parents=True, exist_ok=True)
    analysis_path = args.output / "analysis.json"
    if reuse_analysis(analysis_path, identity):
        print("Unchanged analysis reused:", args.version)
        return
    patches = [analyze_patch(patch, args.version, baseline, original) for patch in PATCHES]
    if args.version == "1.0.928.2":
        # Keep the already reviewed payload adapters and exact combination hashes.
        metadata = next(args.archive.glob(f"browsers/{args.version}/*/browser.json"))
        build_candidate(
            argparse.Namespace(version=args.version, dll=metadata.parent / record["dll"], settings=None, output=args.output)
        )
        for patch in patches:
            if patch["id"] == "bookmark-folder-appearance":
                patch.update(analysis_passed=True, reason="Existing exact-hash reviewed native adapter.")
    report = {
        "schema_version": 1,
        "browser_version": args.version,
        "identity": identity,
        "package_sha256": record["package_sha256"],
        "patches": patches,
        "runtime_passed": False,
    }
    analysis_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.version == "1.0.928.2":
        candidate_path = args.output / "candidate.json"
        candidate = json.loads(candidate_path.read_text())
        package = json.loads((args.output / "package.unsigned.json").read_bytes())
        candidate.update(
            schema_version=2,
            analysis_identity=identity,
            runtime_selections=smoke_selections(package["builds"][0]["states"], package["patches"]),
        )
        candidate_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
        return
    if not any(patch["analysis_passed"] for patch in patches):
        availability = {
            "schema_version": 1,
            "minimum_engine_version": 1,
            "builds": [
                {
                    "version": args.version,
                    "files": {
                        "dll": {
                            "path": f"%ProgramFiles%/Aside/Application/{args.version}/Aside.dll",
                            "original_sha256": sha256(original),
                        }
                    },
                    "states": [{"targets": [], "sha256": {"dll": sha256(original)}, "payloads": {"dll": delta(original, original)}}],
                }
            ],
            "patches": [{key: value for key, value in patch.items() if key != "sites"} for patch in patches],
        }
        # This read-only availability entry advertises no enableable selections.
        build_from_package(availability, args.version)
        (args.output / "availability.json").write_bytes(canonical(availability))
        print("No safely resolved patches:", args.version)
        return
    states = []
    for targets in selections(patches):
        after = render_selection(original, patches, targets)
        states.append({"targets": list(targets), "sha256": {"dll": sha256(after)}, "payloads": {"dll": delta(original, after)}})
    raw = {
        "version": args.version,
        "files": {"dll": {"path": f"%ProgramFiles%/Aside/Application/{args.version}/Aside.dll", "original_sha256": sha256(original)}},
        "states": states,
    }
    metadata = [{key: value for key, value in patch.items() if key != "sites"} for patch in patches]
    for patch in metadata:
        if patch["analysis_passed"]:
            patch["status"] = "supported"  # Private candidate only; signing requires smoke evidence.
    package = {"schema_version": 1, "minimum_engine_version": 1, "builds": [raw], "patches": metadata, "blobs": _intern_payloads(raw)}
    _validate_package(package, args.version, {"dll": original})
    data = canonical(package)
    (args.output / "package.unsigned.json").write_bytes(data)
    supported = sorted(patch["id"] for patch in patches if patch["analysis_passed"])
    # All native patches share the DLL; test the combined selection as an interaction set.
    runtime_sets = smoke_selections(states, metadata)
    candidate = {
        "schema_version": 2,
        "candidate_sha256": sha256(data),
        "browser_version": args.version,
        "source_commit": os.environ.get("GITHUB_SHA", "local-uncommitted"),
        "static_passed": True,
        "runtime_passed": False,
        "required_runtime_checks": list(REQUIRED_RUNTIME_CHECKS),
        "combination_count": len(states),
        "runtime_selections": runtime_sets,
        "analysis_identity": identity,
    }
    (args.output / "candidate.json").write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    print("Candidate prepared:", args.version, ", ".join(supported))


def archived_original(archive: Path, version: str) -> tuple:
    """Require a unique verified archive and recheck its immutable DLL digest."""
    from scripts.patch_analysis import version_tuple

    version_tuple(version)
    matches = list(archive.glob(f"browsers/{version}/*/browser.json"))
    if len(matches) != 1:
        raise ValueError(f"Need one verified official archive for {version}")
    metadata = matches[0]
    record = json.loads(metadata.read_text())
    path = (metadata.parent / record["dll"]).resolve()
    if not path.is_relative_to(metadata.parent.resolve()):
        raise ValueError("Archived DLL escaped its package directory")
    image = path.read_bytes()
    if record.get("verified") is not True or record["actual_browser_version"] != version or sha256(image) != record["dll_sha256"]:
        raise ValueError("Archived version or original digest mismatch")
    return record, image


def passing_subset(args: argparse.Namespace) -> None:
    """Remove failing patches/interactions, then require a fresh smoke report for that package."""
    from scripts.patch_analysis import smoke_selections

    source = args.candidate / "package.unsigned.json"
    data = source.read_bytes()
    runtime = json.loads(args.runtime.read_text())
    if runtime.get("candidate_sha256") != sha256(data) or runtime.get("restored") is not True:
        raise ValueError("Subset requires evidence for this exact candidate and safe restoration")
    package = json.loads(data)
    passed = {
        item["targets"][0] for item in runtime.get("combinations", []) if item.get("passed") is True and len(item["targets"]) == 1
    }
    if not passed:
        raise ValueError("No individually passing patches to publish")
    conflicts = set()
    for item in runtime.get("combinations", []):
        if item.get("passed") is not True and len(item["targets"]) > 1:
            from itertools import combinations

            conflicts.update(tuple(sorted(pair)) for pair in combinations(item["targets"], 2))
    for patch in package["patches"]:
        if patch["id"] not in passed:
            patch.update(status="awaiting validation", reason="Not independently smoke-tested or failed its smoke check.")
        else:
            patch["conflicts"] = sorted(
                set(patch["conflicts"])
                | {other for pair in conflicts if patch["id"] in pair for other in pair if other != patch["id"]}
            )
    for build in package["builds"]:
        build["states"] = [
            state
            for state in build["states"]
            if set(state["targets"]) <= passed and not any(set(pair) <= set(state["targets"]) for pair in conflicts)
        ]
    raw = canonical(package)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "package.unsigned.json").write_bytes(raw)
    original_candidate = json.loads((args.candidate / "candidate.json").read_text())
    candidate = dict(
        original_candidate,
        schema_version=2,
        candidate_sha256=sha256(raw),
        derived_from=sha256(data),
        runtime_passed=False,
        combination_count=len(package["builds"][0]["states"]),
        runtime_selections=smoke_selections(package["builds"][0]["states"], package["patches"]),
    )
    (args.output / "candidate.json").write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")
    build_from_package(package, candidate["browser_version"])
    print("Passing subset prepared; fresh smoke validation required:", ", ".join(sorted(passed)))


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
    if candidate.get("schema_version") == 2:
        from scripts.patch_analysis import smoke_selections

        if runtime.get("restored") is not True:
            raise ValueError("Smoke report does not confirm exact original restoration")
        transactions = {tuple(sorted(item["targets"])) for item in runtime.get("transactions", []) if item.get("passed") is True}
        package = json.loads(package_path.read_bytes())
        required_runtime = {tuple(targets) for targets in smoke_selections(package["builds"][0]["states"], package["patches"])}
        if required_runtime != {tuple(sorted(targets)) for targets in candidate["runtime_selections"]}:
            raise ValueError("Candidate smoke plan omits required individual or interaction checks")
        if transactions != expected or observed != required_runtime:
            raise ValueError("Missing combination transactions or required patch/interaction smoke checks")
        for item in combinations:
            screenshots = item.get("screenshots", [])
            if not screenshots or any(not _evidence_digest(args.runtime.parent, evidence) for evidence in screenshots):
                raise ValueError("Smoke screenshots are missing or altered")
    elif observed != expected:
        raise ValueError("Runtime report must cover every advertised combination")
    return digest


def _evidence_digest(root: Path, evidence: dict) -> bool:
    """Bind portable screenshot paths and their contents to the smoke report."""
    path = (root / evidence["path"]).resolve()
    return path.is_relative_to(root.resolve()) and path.is_file() and file_sha256(path) == evidence["sha256"]


def bundle_candidate(args: argparse.Namespace) -> None:
    """Add exact smoke-tested data to source-bundled support without publishing a release."""
    digest = _tested_digest(args)
    candidate = json.loads((args.candidate / "candidate.json").read_text())
    version = candidate["browser_version"]
    from scripts.patch_analysis import version_tuple

    version_tuple(version)
    package = (args.candidate / "package.unsigned.json").read_bytes()
    build_from_package(json.loads(package), version)
    args.output.mkdir(parents=True, exist_ok=True)
    index_path = args.output / "index.json"
    index = json.loads(index_path.read_bytes()) if index_path.exists() else {"schema_version": 1, "builds": {}}
    target = args.output / f"{version}.json"
    if target.exists() and target.read_bytes() != package:
        raise ValueError("Existing bundled support differs; review adapter migration before replacing it")
    target.write_bytes(package)
    index["builds"][version] = {"sha256": digest, "runtime_report_sha256": file_sha256(args.runtime)}
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print("Smoke-tested support bundled:", version)


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
    analyze = commands.add_parser("analyze")
    analyze.add_argument("--archive", type=Path, required=True)
    analyze.add_argument("--version", required=True)
    analyze.add_argument("--output", type=Path, required=True)
    subset = commands.add_parser("subset")
    for name in ("candidate", "runtime", "output"):
        subset.add_argument("--" + name, type=Path, required=True)
    bundle = commands.add_parser("bundle")
    for name in ("candidate", "runtime", "output"):
        bundle.add_argument("--" + name, type=Path, required=True)
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
    {
        "build": build_candidate,
        "analyze": analyze_candidate,
        "subset": passing_subset,
        "bundle": bundle_candidate,
        "keygen": keygen,
        "sign": sign_candidate,
    }[args.command](args)


if __name__ == "__main__":
    main()
