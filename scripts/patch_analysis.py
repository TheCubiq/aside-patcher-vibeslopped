"""Resolve patch candidates without granting installation or publication approval."""

import re
import struct
from itertools import combinations

from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_GRP_CALL, CS_GRP_JUMP
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_RIP

from aside_patcher.common.utils import sha256
from aside_patcher.common.exceptions import Refusal
from aside_patcher.pe import file_offset, parse_layout


def version_tuple(value: str) -> tuple:
    """Compare numeric versions rather than lexical strings."""
    if not re.fullmatch(r"\d+(?:\.\d+){3}", value):
        raise ValueError("Expected a four-part numeric browser version")
    return tuple(map(int, value.split(".")))


def eligibility(definition, version: str) -> str | None:
    """Return a range exclusion without treating a range as binary proof."""
    current = version_tuple(version)
    policy = definition.compatibility
    for key, comparison in (("minimum_version", lambda a, b: a < b), ("maximum_version", lambda a, b: a > b)):
        if policy.get(key) and comparison(current, version_tuple(policy[key])):
            return f"Outside declared {key.replace('_', ' ')} {policy[key]}"
    return None


def instructions(data: bytes, address: int = 0) -> list:
    """Require complete x64 instruction decoding for a candidate region."""
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    decoder.detail = True
    result = list(decoder.disasm(data, address))
    if sum(item.size for item in result) != len(data):
        raise ValueError("Patch region is not a complete x64 instruction sequence")
    return result


def masked_bytes(data: bytes) -> tuple[bytes, set]:
    """Mask only relative code/data references; retain registers and field offsets."""
    mask = set()
    for item in instructions(data):
        start = item.address
        if item.group(CS_GRP_CALL) or item.group(CS_GRP_JUMP):
            if item.imm_size:
                mask.update(range(start + item.imm_offset, start + item.imm_offset + item.imm_size))
        if any(operand.type == X86_OP_MEM and operand.mem.base == X86_REG_RIP for operand in item.operands):
            # x64 RIP-relative addressing always encodes disp32. Capstone can
            # incorrectly report disp_size=2 for prefixed SIMD instructions.
            displacement = item.bytes[item.disp_offset : item.disp_offset + 4]
            if len(displacement) != 4 or int.from_bytes(displacement, "little", signed=True) != item.disp:
                raise ValueError("RIP displacement encoding disagrees with disassembly")
            mask.update(range(start + item.disp_offset, start + item.disp_offset + 4))
    pattern = b"".join(b"." if index in mask else re.escape(bytes([value])) for index, value in enumerate(data))
    return pattern, mask


def code_sections(image: bytes, layout) -> list:
    """Restrict instruction candidates to executable PE sections."""
    return [
        section
        for index, section in enumerate(layout.sections)
        if struct.unpack_from("<I", image, layout.section_table + index * 40 + 36)[0] & 0x20000000
    ]


def offset_rva(layout, offset: int) -> int:
    """Translate a file position only when it lies inside a mapped section."""
    for section in layout.sections:
        if section.file_offset <= offset < section.file_offset + section.raw_size:
            return section.rva + offset - section.file_offset
    raise ValueError("Candidate site is outside mapped PE sections")


def context_bytes(image: bytes, offset: int) -> bytes:
    """Take a bounded sequence beginning at a known instruction boundary."""
    decoder = Cs(CS_ARCH_X86, CS_MODE_64)
    result = bytearray()
    for item in decoder.disasm(image[offset : offset + 160], 0):
        result.extend(item.bytes)
        if len(result) >= 96 or item.mnemonic == "ret":
            break
    if len(result) < 24:
        raise ValueError("Insufficient instruction context")
    return bytes(result)


def reference_targets(data: bytes, address: int) -> list:
    """Collect direct branches and RIP references for independent target checks."""
    targets = []
    for item in instructions(data, address):
        for operand in item.operands:
            if operand.type == X86_OP_IMM and (item.group(CS_GRP_CALL) or item.group(CS_GRP_JUMP)):
                targets.append(("code", operand.imm))
            elif operand.type == X86_OP_MEM and operand.mem.base == X86_REG_RIP:
                targets.append(("data", item.address + item.size + operand.mem.disp))
    return targets


# Keep source/target instruction validation together for review.
# pylint: disable-next=too-many-arguments,too-many-positional-arguments,too-many-locals
def validate_references(baseline: bytes, image: bytes, old_layout, layout, old_site: int, site: int, size: int) -> None:
    """Check local branch topology and external code/data anchors before reuse."""
    old_rva, rva = offset_rva(old_layout, old_site), offset_rva(layout, site)
    old_targets = reference_targets(baseline[old_site : old_site + size], old_rva)
    targets = reference_targets(image[site : site + size], rva)
    if len(old_targets) != len(targets):
        raise ValueError("Instruction reference count changed")
    executable = code_sections(image, layout)
    for (old_kind, old_target), (kind, target) in zip(old_targets, targets):
        if kind != old_kind:
            raise ValueError("Instruction reference type changed")
        if old_rva <= old_target < old_rva + size:
            if target - rva != old_target - old_rva:
                raise ValueError("Local branch topology changed")
            continue
        old_offset, offset = file_offset(old_layout, old_target), file_offset(layout, target)
        if kind == "code":
            if not any(section.rva <= target < section.rva + section.raw_size for section in executable):
                raise ValueError("Branch target is not executable")
            anchor = context_bytes(baseline, old_offset)
            if not re.fullmatch(masked_bytes(anchor)[0], image[offset : offset + len(anchor)], re.DOTALL):
                raise ValueError("External branch target changed")
        elif baseline[old_offset : old_offset + 32] != image[offset : offset + 32]:
            old_base = struct.unpack_from("<Q", baseline, old_layout.optional_header + 24)[0]
            base = struct.unpack_from("<Q", image, layout.optional_header + 24)[0]
            old_pointer = struct.unpack_from("<Q", baseline, old_offset)[0] - old_base
            pointer = struct.unpack_from("<Q", image, offset)[0] - base
            # CFG dispatch slots contain relocated pointers to executable stubs.
            # Do not compare neighboring slots or assume a matching slot RVA is proof.
            if not any(section.rva <= pointer < section.rva + section.raw_size for section in executable):
                raise ValueError("Referenced data anchor changed")
            old_stub, stub = file_offset(old_layout, old_pointer), file_offset(layout, pointer)
            if baseline[old_stub : old_stub + 16] != image[stub : stub + 16]:
                raise ValueError("Indirect dispatch target changed")


def relocate_replacement(before: bytes, template_before: bytes, template_after: bytes, address: int) -> bytes:
    """Preserve relocated operands, including a rewritten conditional branch target."""
    if len(before) != len(template_before) or len(before) != len(template_after):
        raise ValueError("Instruction-site patches must preserve region size")
    result = bytearray(new if old != new else actual for actual, old, new in zip(before, template_before, template_after))
    old_branches = [item for item in instructions(before, address) if item.group(CS_GRP_JUMP) and item.imm_size]
    new_branches = [item for item in instructions(bytes(result), address) if item.group(CS_GRP_JUMP) and item.imm_size]
    if len(old_branches) != len(new_branches):
        raise ValueError("Replacement changes unsupported branch structure")
    for old, new in zip(old_branches, new_branches):
        target = next(operand.imm for operand in old.operands if operand.type == X86_OP_IMM)
        displacement = target - (new.address + new.size)
        start = new.address - address + new.imm_offset
        result[start : start + new.imm_size] = displacement.to_bytes(new.imm_size, "little", signed=True)
    instructions(bytes(result), address)
    return bytes(result)


def analyze_instruction_sites(definition, baseline: bytes, image: bytes) -> list:
    """Resolve every dependency of a simple patch, or reject the whole patch."""
    layout, old_layout = parse_layout(image), parse_layout(baseline)
    resolved = []
    for site in definition.files["dll"]["sites"]:
        old_offset = site["offset"]
        before, after = bytes.fromhex(site["before"]), bytes.fromhex(site["after"])
        if baseline[old_offset : old_offset + len(before)] != before:
            raise ValueError("Analyzer baseline does not match the reviewed patch")
        context = context_bytes(baseline, old_offset)
        pattern = re.compile(masked_bytes(context)[0], re.DOTALL)
        hits = [
            match.start()
            for section in code_sections(image, layout)
            for match in pattern.finditer(image, section.file_offset, section.file_offset + section.raw_size)
        ]
        if len(hits) != 1:
            raise ValueError(f"Site 0x{old_offset:X}: expected one context match, found {len(hits)}")
        offset = hits[0]
        actual = image[offset : offset + len(before)]
        if not re.fullmatch(masked_bytes(before)[0], actual, re.DOTALL):
            raise ValueError("Patch instruction structure changed")
        validate_references(baseline, image, old_layout, layout, old_offset, offset, len(context))
        replacement = relocate_replacement(actual, before, after, offset_rva(layout, offset))
        resolved.append(
            {
                "offset": offset,
                "before": actual.hex(),
                "after": replacement.hex(),
                "context_sha256": sha256(image[offset : offset + len(context)]),
                "context_size": len(context),
            }
        )
    return resolved


def analyze_patch(definition, version: str, baseline: bytes, image: bytes) -> dict:
    """Return per-patch evidence without treating a candidate as runtime-tested."""
    result = {
        "id": definition.patch_id,
        "description": definition.description,
        "conflicts": list(definition.conflicts),
        "files": list(definition.files),
        "status": "awaiting validation",
        "analysis_passed": False,
        "smoke": definition.smoke,
    }
    exclusion = eligibility(definition, version)
    if exclusion:
        return dict(result, status="unsupported", reason=exclusion)
    if definition.patch_id == "bookmark-folder-appearance":
        from aside_patcher.patches.bookmark_folder_appearance.adapters import UnknownNativeBuild, load_adapter

        try:
            adapter, _, _ = load_adapter(image)
            if adapter["version"] != version:
                raise ValueError("Bookmark adapter version disagrees with the archive")
            return dict(
                result,
                analysis_passed=True,
                sites=[],
                native_adapter=adapter["original_sha256"],
                reason="Exact native adapter resolved; candidate runtime checks pending.",
            )
        except UnknownNativeBuild:
            from scripts.native_discovery import discover_bookmarks

            try:
                discovery = discover_bookmarks(baseline, image)
                return dict(
                    result,
                    native_discovery=discovery,
                    reason=f"Native discovery: {discovery['structural_matches']} code and {discovery['data_matches']} data matches; "
                    f"{discovery['remaining']} dependencies require validation. No installable adapter generated.",
                )
            except (ValueError, Refusal) as error:
                return dict(result, reason=str(error))
        except (ValueError, Refusal) as error:
            return dict(result, reason=str(error))
    if definition.compatibility.get("analyzer") != "instruction-sites":
        reason = (
            "Requires verified native bindings, object layouts and payload relocation; hook matches alone are insufficient."
            if "appearance" not in definition.files
            else "Requires independent native toggle and settings-component adapters."
        )
        return dict(result, reason=reason)
    unknown_requirements = set(definition.compatibility.get("requires", [])) - {"native-toolbar", "native-sidebar"}
    if unknown_requirements:
        return dict(result, reason="No verifier for required capabilities: " + ", ".join(sorted(unknown_requirements)))
    try:
        sites = analyze_instruction_sites(definition, baseline, image)
        return dict(result, analysis_passed=True, sites=sites, reason="Resolved instruction sites; runtime smoke check pending.")
    except (ValueError, OverflowError, Refusal) as error:
        return dict(result, reason=str(error))


def selections(patches: list) -> list:
    """Enumerate nonconflicting selections from independently resolved patches."""
    available = [item for item in patches if item["analysis_passed"]]
    result = []
    for count in range(len(available) + 1):
        for group in combinations(available, count):
            ids = {item["id"] for item in group}
            if not any(ids.intersection(item["conflicts"]) for item in group):
                result.append(tuple(sorted(ids)))
    return result


def render_selection(original: bytes, patches: list, targets: tuple) -> bytes:
    """Apply checked nonoverlapping sites to one immutable vendor original."""
    sites = sorted((site for patch in patches if patch["id"] in targets for site in patch["sites"]), key=lambda site: site["offset"])
    native = [patch for patch in patches if patch["id"] in targets and patch.get("native_adapter")]
    if native:
        from aside_patcher.patches.bookmark_folder_appearance.native import build_native_appearance

        output = bytearray(build_native_appearance(original))
    else:
        output = bytearray(original)
    end = 0
    for site in sites:
        offset = site["offset"]
        before, after = bytes.fromhex(site["before"]), bytes.fromhex(site["after"])
        if offset < end or output[offset : offset + len(before)] != before:
            raise ValueError("Overlapping patch sites or changed original bytes")
        output[offset : offset + len(after)] = after
        end = offset + len(before)
    return bytes(output)


def smoke_selections(states: list, patches: list) -> list:
    """Cover single patches and interacting pairs without launching every selection."""
    supported = {patch["id"]: patch for patch in patches if patch.get("status") == "supported"}
    valid = [tuple(sorted(state["targets"])) for state in states]
    singles = {selection for selection in valid if len(selection) == 1}
    pairs = {
        tuple(sorted((left, right)))
        for left, right in combinations(supported, 2)
        if set(supported[left]["files"]).intersection(supported[right]["files"])
        and any({left, right}.issubset(selection) for selection in valid)
    }
    selected = set(singles)
    for selection in sorted(valid, key=lambda value: (-len(value), value)):
        covered = {pair for pair in pairs if set(pair).issubset(selection)}
        if covered:
            selected.add(selection)
            pairs -= covered
    return [list(selection) for selection in sorted(selected, key=lambda value: (len(value), value))]
