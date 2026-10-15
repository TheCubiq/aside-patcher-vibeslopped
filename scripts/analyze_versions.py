"""Compare archived originals; signatures generate candidates, never support claims."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_GRP_CALL, CS_GRP_JUMP
from capstone.x86 import X86_REG_RIP
from aside_patcher.pe import parse_layout, file_offset
from aside_patcher.patches.bookmark_folder_appearance.native import HOOKS
from aside_patcher.patches import PATCHES


def _address_mask(instruction) -> set[int]:
    mask = set()
    if CS_GRP_CALL in instruction.groups or CS_GRP_JUMP in instruction.groups:
        mask.update(range(instruction.imm_offset, instruction.imm_offset + instruction.imm_size))
    if any(operand.type == 3 and operand.mem.base == X86_REG_RIP for operand in instruction.operands):
        mask.update(range(instruction.disp_offset, instruction.disp_offset + instruction.disp_size))
    return mask


def signature(data: bytes, offset: int, count: int = 24) -> re.Pattern:
    """Mask relocation operands while retaining structure/field access operands."""
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True
    pieces = []
    for instruction in decoder.disasm(data[offset : offset + 512], 0):
        mask = _address_mask(instruction)
        pieces.extend(b"." if index in mask else re.escape(bytes([value])) for index, value in enumerate(instruction.bytes))
        if instruction.mnemonic == "ret" or len(pieces) >= count * 4:
            break
    if not pieces:
        raise ValueError("Disassembly produced no anchor")
    return re.compile(b"".join(pieces), re.DOTALL)


def _archives(root: Path) -> list[tuple]:
    records = []
    for metadata in root.glob("browsers/*/*/browser.json"):
        record = json.loads(metadata.read_text())
        if record["verified"]:
            records.append((record, metadata.parent / record["dll"]))
    return sorted(records, key=lambda item: item[0]["actual_browser_version"])


def _dependencies() -> list[dict]:
    result = []
    root = Path(__file__).resolve().parents[1] / "aside_patcher/patches/bookmark_folder_appearance"
    for source in root.glob("native_*.c"):
        for line, text in enumerate(source.read_text().splitlines(), 1):
            addresses = re.findall(r"BfaNativeAddress\((0x[0-9a-f]+)\)", text)
            fields = re.findall(r"(?:\+|\[)(0x[0-9a-f]+)", text)
            if addresses or fields:
                result.append(dict(source=source.name, line=line, native_addresses=addresses, layout_operands=fields))
    return result


def _code_sections(image: bytes, layout) -> list:
    return [
        section
        for index, section in enumerate(layout.sections)
        if struct.unpack_from("<I", image, layout.section_table + index * 40 + 36)[0] & 0x20000000
    ]


def _hook_result(image: bytes, layout, patterns: dict, hook: tuple) -> dict:
    name, rva, expected, *_ = hook
    hits = []
    for section in _code_sections(image, layout):
        for match in patterns[name].finditer(image, section.file_offset, section.file_offset + section.raw_size):
            hits.append(section.rva + match.start() - section.file_offset)
    unchanged = False
    if len(hits) == 1:
        offset = file_offset(layout, hits[0])
        unchanged = image[offset : offset + len(bytes.fromhex(expected))] == bytes.fromhex(expected)
    return dict(name=name, baseline_rva=rva, matches=hits, unique=len(hits) == 1, prologue_unchanged=unchanged)


def _byte_patches(image: bytes) -> list[dict]:
    result = []
    for patch in PATCHES:
        sites = patch.files.get("dll", {}).get("sites", [])
        if sites:
            matches = [
                dict(
                    baseline_offset=site["offset"],
                    exact_matches=[match.start() for match in re.finditer(re.escape(bytes.fromhex(site["before"])), image)],
                )
                for site in sites
            ]
            result.append(dict(id=patch.patch_id, sites=matches))
    return result


def _version_result(record: dict, path: Path, patterns: dict, baseline: Path) -> dict:
    image = path.read_bytes()
    if hashlib.sha256(image).hexdigest() != record["dll_sha256"]:
        raise ValueError("Archived DLL changed after verification")
    layout = parse_layout(image)
    return dict(
        version=record["actual_browser_version"],
        dll_sha256=record["dll_sha256"],
        package_sha256=record["package_sha256"],
        source=record["url"],
        signature=record["evidence"],
        status="baseline verified" if path == baseline else "awaiting validation",
        hooks=[_hook_result(image, layout, patterns, hook) for hook in HOOKS],
        byte_patches=_byte_patches(image),
    )


def main() -> None:
    """Write a reproducible report without approving any new binary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    archives = _archives(args.archive)
    base_record, baseline = next(item for item in archives if item[0]["actual_browser_version"] == "1.0.928.2")
    original = baseline.read_bytes()
    if hashlib.sha256(original).hexdigest() != base_record["dll_sha256"]:
        raise ValueError("Baseline DLL changed after verification")
    layout = parse_layout(original)
    patterns = {name: signature(original, file_offset(layout, rva)) for name, rva, *_ in HOOKS}
    results = [_version_result(record, path, patterns, baseline) for record, path in archives]
    report = dict(
        schema_version=1,
        installable=False,
        baseline="1.0.928.2",
        versions=results,
        bookmark_dependencies=_dependencies(),
        remaining_validation=[
            "All native bindings and object layouts",
            "Control flow and calling conventions",
            "Native icon/color resources",
            "Interactive per-patch and combination checks",
        ],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for result in results:
        print(result["version"], sum(hook["unique"] for hook in result["hooks"]), "/", len(HOOKS), "unique hook candidates")


if __name__ == "__main__":
    main()
