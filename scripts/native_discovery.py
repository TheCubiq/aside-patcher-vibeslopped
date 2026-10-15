"""Discover native binding candidates; never authorize installation or publication."""

# Direct script execution needs the repository root before package imports.
# pylint: disable=wrong-import-position,import-outside-toplevel
# flake8: noqa: E402

import argparse
from bisect import bisect_right
import json
from pathlib import Path
import re
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capstone import Cs, CS_ARCH_X86, CS_MODE_64

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.utils import sha256
from aside_patcher.pe import file_offset, parse_layout
from scripts.patch_analysis import code_sections, masked_bytes, offset_rva, reference_targets
from scripts.native_resources import discover_resources


def runtime_functions(image: bytes, layout) -> dict:
    """Read complete x64 exception ranges, rejecting malformed table boundaries."""
    rva, size = struct.unpack_from("<II", image, layout.optional_header + 136)
    if size % 12 or not size:
        raise ValueError("Missing or malformed native runtime function table")
    offset = file_offset(layout, rva)
    if offset + size > len(image):
        raise ValueError("Runtime function table exceeds image")
    return {start: end for start, end, _ in struct.iter_unpack("<III", image[offset : offset + size])}


# The paired images, PE layouts and indexes remain explicit to avoid mixing builds.
# pylint: disable-next=too-many-instance-attributes
class NativeDiscovery:
    """Match full functions while retaining registers, fields and local control flow."""

    def __init__(self, baseline: bytes, image: bytes, timeout_seconds: int = 120):
        if not 1 <= timeout_seconds <= 300:
            raise ValueError("Native discovery budget must be between 1 and 300 seconds")
        self.started = time.monotonic()
        self.deadline = self.started + timeout_seconds
        self.baseline, self.image = baseline, image
        self.old_layout, self.layout = parse_layout(baseline), parse_layout(image)
        self.functions = runtime_functions(baseline, self.old_layout)
        self.new_functions = runtime_functions(image, self.layout)
        self.starts = sorted(self.functions)
        self.sections = code_sections(image, self.layout)
        self.old_sections = code_sections(baseline, self.old_layout)
        self.references = {}
        self.cache = {}
        self.method_patterns = {}
        self.method_matches = {}
        self.method_failures = {}

    def check_budget(self):
        """Return partial evidence when a bounded discovery budget expires."""
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Native discovery time budget exceeded")

    def executable(self, rva: int) -> bool:
        """Classify using baseline section flags rather than address magnitude."""
        return any(section.rva <= rva < section.rva + section.raw_size for section in self.old_sections)

    def function_body(self, rva: int) -> tuple[int, bytes]:
        """Use unwind boundaries, or a bounded leaf body ending in RET."""
        index = bisect_right(self.starts, rva) - 1
        if index >= 0:
            start = self.starts[index]
            end = self.functions[start]
            if start <= rva < end:
                offset = file_offset(self.old_layout, start)
                return start, self.baseline[offset : offset + end - start]
        offset = file_offset(self.old_layout, rva)
        body = bytearray()
        branching = False
        for instruction in Cs(CS_ARCH_X86, CS_MODE_64).disasm(self.baseline[offset : offset + 512], rva):
            body.extend(instruction.bytes)
            if instruction.mnemonic == "ret":
                return rva, bytes(body)
            if instruction.mnemonic == "jmp" and not branching and len(body) <= 32:
                return rva, bytes(body)
            branching |= instruction.mnemonic.startswith("j") or instruction.mnemonic == "call"
        raise ValueError("No unwind boundary or complete bounded leaf body")

    def match_function(self, rva: int) -> dict:
        """Require a unique complete body and matching local branch topology."""
        self.check_budget()
        start, body = self.function_body(rva)
        if start in self.cache:
            result = dict(self.cache[start])
            if result.get("target_rva") is not None:
                result["target_rva"] += rva - start
            if "matches" in result:
                result["matches"] = [target + rva - start for target in result["matches"]]
            return result
        pattern = re.compile(masked_bytes(body)[0], re.DOTALL)
        hits = [
            offset_rva(self.layout, match.start())
            for section in self.sections
            for match in pattern.finditer(self.image, section.file_offset, section.file_offset + section.raw_size)
        ]
        if start in self.functions:
            hits = [hit for hit in hits if self.new_functions.get(hit) == hit + len(body)]
        if len(hits) != 1:
            result = {"status": "unresolved", "reason": f"Complete function has {len(hits)} matches", "matches": hits}
            self.cache[start] = result
            return dict(result, matches=[target + rva - start for target in hits])
        target = hits[0]
        offset = file_offset(self.layout, target)
        old_refs = reference_targets(body, start)
        new_refs = reference_targets(self.image[offset : offset + len(body)], target)
        if len(old_refs) != len(new_refs):
            raise ValueError("Function reference count changed")
        for (kind, old), (new_kind, new) in zip(old_refs, new_refs):
            if kind != new_kind or (start <= old < start + len(body) and new - target != old - start):
                raise ValueError("Function reference topology changed")
            self.references.setdefault(old, set()).add(new)
        result = {"status": "structural-match", "target_rva": target, "function_bytes": len(body)}
        self.cache[start] = result
        return dict(result, target_rva=target + rva - start)

    def discover(self, dependencies: list[int], resources: bool = False) -> dict:
        """Return explicit unresolved dependencies instead of inferring blanket support."""
        results = {}
        for rva in sorted(set(dependencies)):
            if not self.executable(rva):
                continue
            try:
                results[rva] = self.match_function(rva)
            except (ValueError, Refusal, TimeoutError, struct.error) as error:
                results[rva] = {"status": "unresolved", "reason": str(error)}
        for rva, result in list(results.items()):
            targets = self.references.get(rva, set())
            if result["status"] == "unresolved" and len(targets) == 1:
                target = next(iter(targets))
                if target in result.get("matches", []):
                    results[rva] = {
                        "status": "structural-match",
                        "target_rva": target,
                        "evidence": "Complete body and unique reference from a matched native caller",
                    }
        for rva in sorted(set(dependencies) - results.keys()):
            targets = sorted(self.references.get(rva, set()))
            # References establish location only. Pointer tables, resource data
            # and writable globals require independent type-specific validation.
            results[rva] = {
                "status": "awaiting-data-validation" if len(targets) == 1 else "unresolved",
                "matches": targets,
                "reason": "Native data identity and layout require independent validation",
            }
        resource_error = None
        if resources:
            try:
                discover_resources(self, results)
            except (ValueError, Refusal, TimeoutError, struct.error) as error:
                resource_error = str(error)
        return {
            "schema_version": 1,
            "installable": False,
            "elapsed_seconds": round(time.monotonic() - self.started, 2),
            "resource_error": resource_error,
            "method_failures": self.method_failures,
            "baseline_sha256": sha256(self.baseline),
            "original_sha256": sha256(self.image),
            "dependencies": {f"0x{rva:x}": result for rva, result in results.items()},
            "candidate_map": {
                f"0x{rva:x}": f"0x{result['target_rva']:x}"
                for rva, result in results.items()
                if result["status"] in ("structural-match", "data-match")
            },
            "requires_validation": ["complete-dependency-map", "payload-rebuild", "runtime-smoke", "owner-approval"],
            "structural_matches": sum(item["status"] == "structural-match" for item in results.values()),
            "data_matches": sum(item["status"] == "data-match" for item in results.values()),
            "remaining": sum(item["status"] not in ("structural-match", "data-match") for item in results.values()),
        }


def discover_bookmarks(baseline: bytes, image: bytes) -> dict:
    """Reuse reviewed dependency identities, never the target build's address map."""
    from scripts.freeze_bookmark_payload import BINDINGS

    root = Path(__file__).resolve().parents[1] / "compatibility/adapters"
    baseline_adapter = json.loads((root / "1.0.928.2.json").read_bytes())
    reviewed_bytes = (root / "1.0.922.1-bookmarks.json").read_bytes()
    reviewed = json.loads(reviewed_bytes)
    if sha256(baseline) != baseline_adapter["original_sha256"]:
        raise ValueError("Native discovery baseline does not match reviewed bindings")
    dependencies = [int(key, 16) for key in reviewed["native_rvas"]]
    dependencies.extend(hook["rva"] for hook in baseline_adapter["bookmark_hooks"])
    dependencies.extend(rva for region, rva in BINDINGS.values() if region == "native" and rva)
    report = NativeDiscovery(baseline, image).discover(dependencies, resources=True)
    report["dependency_manifest_sha256"] = sha256(reviewed_bytes)
    return report


def main() -> None:
    """Discover archived builds without modifying browser files or approved adapters."""
    from scripts.compatibility_release import archived_original

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _, baseline = archived_original(args.archive, "1.0.928.2")
    _, image = archived_original(args.archive, args.version)
    report = discover_bookmarks(baseline, image)
    report["version"] = args.version
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(
        f"{args.version}: {report['structural_matches']} code matches, "
        f"{report['data_matches']} data matches; {report['remaining']} dependencies pending"
    )


if __name__ == "__main__":
    main()
