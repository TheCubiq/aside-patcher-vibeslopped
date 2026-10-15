"""Validate reviewed bookmark resource types independently of code signatures."""

import re
import struct

from aside_patcher.pe import file_offset
from scripts.patch_analysis import masked_bytes, reference_targets, instructions

# Reviewed baseline regions, not candidate addresses. A changed shape is refused.
VTABLES = {0xFB322B0: 0x140, 0xFB5E328: 0x250, 0xFDF7F98: 0x380, 0xFE16378: 64}
VECTOR_ICONS = (0x1055F3B8, 0x1055F920, 0x10560A28, 0x10563AB8, 0x10C6AA10, 0x10C6A378, 0x10C6AB38)


def read(image, layout, rva, size):
    """Require a complete bounded resource span."""
    if not 0 < size <= 1024 * 1024:
        raise ValueError("Unreasonable native resource span")
    offset = file_offset(layout, rva)
    if not any(
        section.file_offset <= offset and offset + size <= section.file_offset + section.raw_size for section in layout.sections
    ):
        raise ValueError("Native resource crosses its mapped section")
    result = image[offset : offset + size]
    if len(result) != size:
        raise ValueError("Truncated native resource")
    return result


def image_base(image, layout):
    """Read the preferred image base used by relocated data pointers."""
    return struct.unpack_from("<Q", image, layout.optional_header + 24)[0]


def vector_identity(image, layout, rva):
    """Compare vector names, representation counts and every path word."""
    base = image_base(image, layout)
    pointer, count, name = struct.unpack("<QQQ", read(image, layout, rva, 24))
    if not 1 <= count <= 16:
        raise ValueError("Invalid vector representation count")
    name_offset = file_offset(layout, name - base)
    label = image[name_offset : name_offset + 256].split(b"\0", 1)
    if len(label) != 2:
        raise ValueError("Unterminated vector name")
    paths = []
    for index in range(count):
        path, words = struct.unpack("<QQ", read(image, layout, pointer - base + index * 16, 16))
        paths.append(read(image, layout, path - base, words * 4))
    return label[0], tuple(paths)


def readonly_sections(discovery):
    """Search resources only in non-executable, non-writable PE sections."""
    return [
        section
        for index, section in enumerate(discovery.layout.sections)
        if not struct.unpack_from("<I", discovery.image, discovery.layout.section_table + index * 40 + 36)[0] & 0xA0000000
    ]


def find_readonly(discovery, pattern):
    """Yield candidate RVAs rather than confusing raw file offsets with RVAs."""
    compiled = re.compile(pattern, re.DOTALL)
    for section in readonly_sections(discovery):
        discovery.check_budget()
        for match in compiled.finditer(discovery.image, section.file_offset, section.file_offset + section.raw_size):
            discovery.check_budget()
            yield section.rva + match.start() - section.file_offset


def code_equal(discovery, old, target, depth=0):
    """Validate a vtable method's complete body at the proposed destination."""
    discovery.check_budget()
    if depth > 4 or not any(section.rva <= target < section.rva + section.raw_size for section in discovery.sections):
        return False
    key = old, target
    if key in discovery.method_matches:
        return discovery.method_matches[key]
    start, body = discovery.function_body(old)
    new_start = target - (old - start)
    if start in discovery.functions and discovery.new_functions.get(new_start) != new_start + len(body):
        return False
    offset = file_offset(discovery.layout, new_start)
    if start not in discovery.method_patterns:
        discovery.method_patterns[start] = re.compile(masked_bytes(body)[0], re.DOTALL)
    result = bool(discovery.method_patterns[start].fullmatch(discovery.image[offset : offset + len(body)]))
    if not result and len(discovery.method_failures) < 16:
        _, mask = masked_bytes(body)
        candidate = discovery.image[offset : offset + len(body)]
        changed = [index for index, (left, right) in enumerate(zip(body, candidate)) if left != right and index not in mask]
        discovery.method_failures[f"0x{start:x}"] = {
            "target_rva": new_start,
            "reason": "Unmasked bytes changed; requires independent instruction/data validation",
            "changed_offsets": changed[:32],
        }
    # Adjustment thunks have no unwind entry and end in a tail JMP. Matching
    # only its displacement-masked bytes would not establish method identity.
    if result and len(body) <= 32 and instructions(body)[-1].mnemonic == "jmp":
        old_refs = reference_targets(body, start)
        new_refs = reference_targets(discovery.image[offset : offset + len(body)], new_start)
        if len(old_refs) != len(new_refs):
            result = False
        for (kind, left), (new_kind, right) in zip(old_refs, new_refs):
            if kind != new_kind or (kind == "code" and not code_equal(discovery, left, right, depth + 1)):
                result = False
                break
    discovery.method_matches[key] = result
    return result


def table_equal(discovery, old, target, size):
    """Compare every table slot; independently validate moved executable pointers."""
    before = read(discovery.baseline, discovery.old_layout, old, size)
    after = read(discovery.image, discovery.layout, target, size)
    old_base = image_base(discovery.baseline, discovery.old_layout)
    base = image_base(discovery.image, discovery.layout)
    for offset in range(0, size, 8):
        left, right = struct.unpack_from("<Q", before, offset)[0], struct.unpack_from("<Q", after, offset)[0]
        if discovery.executable(left - old_base):
            if not code_equal(discovery, left - old_base, right - base):
                return False
        elif left != right:
            return False
    return True


def locate_table(discovery, old, size):
    """Use method anchors then require one complete matching vtable region."""
    base = image_base(discovery.baseline, discovery.old_layout)
    target_base = image_base(discovery.image, discovery.layout)
    references = discovery.references.get(old, set())
    if len(references) == 1:
        target = next(iter(references))
        if table_equal(discovery, old, target, size):
            return target
    for offset in range(0, size, 8):
        pointer = struct.unpack("<Q", read(discovery.baseline, discovery.old_layout, old + offset, 8))[0] - base
        if not discovery.executable(pointer):
            continue
        try:
            match = discovery.match_function(pointer)
        except (ValueError, RuntimeError):
            continue
        targets = [match["target_rva"]] if "target_rva" in match else match.get("matches", [])
        if not 0 < len(targets) <= 64:
            continue
        hits = set()
        for target in targets:
            for candidate in find_readonly(discovery, re.escape(struct.pack("<Q", target_base + target))):
                try:
                    if table_equal(discovery, old, candidate - offset, size):
                        hits.add(candidate - offset)
                except (ValueError, RuntimeError):
                    continue
        if len(hits) == 1:
            return hits.pop()
        if hits:
            raise ValueError(f"Vtable region has {len(hits)} matches")
    raise ValueError("No independently validated vtable region")


def locate_vector(discovery, old):
    """Search descriptor structure and compare complete semantic vector identity."""
    identity = vector_identity(discovery.baseline, discovery.old_layout, old)
    count = len(identity[1])
    pattern = b".{8}" + re.escape(struct.pack("<Q", count)) + b".{8}"
    hits = []
    for candidate in find_readonly(discovery, pattern):
        try:
            if vector_identity(discovery.image, discovery.layout, candidate) == identity:
                hits.append(candidate)
        except (ValueError, RuntimeError, struct.error):
            continue
    if len(hits) != 1:
        raise ValueError(f"Vector descriptor has {len(hits)} matches")
    return hits[0]


def icon_table_equal(discovery, target):
    """Validate all 48 icon keys, labels and vector paths, never just a string hit."""
    old_base = image_base(discovery.baseline, discovery.old_layout)
    base = image_base(discovery.image, discovery.layout)
    for index in range(48):
        old = struct.unpack("<QQQQQ", read(discovery.baseline, discovery.old_layout, 0x10E552E0 + index * 40, 40))
        new = struct.unpack("<QQQQQ", read(discovery.image, discovery.layout, target + index * 40, 40))
        if old[1] != new[1] or old[4] != new[4]:
            return False
        for position, size in ((0, old[1]), (3, old[4] * 2)):
            if read(discovery.baseline, discovery.old_layout, old[position] - old_base, size) != read(
                discovery.image, discovery.layout, new[position] - base, size
            ):
                return False
        if vector_identity(discovery.baseline, discovery.old_layout, old[2] - old_base) != vector_identity(
            discovery.image, discovery.layout, new[2] - base
        ):
            return False
    return True


def discover_resources(discovery, results):
    """Resolve typed resources; leave unsupported kinds explicitly pending."""
    for old, size in VTABLES.items():
        try:
            target = locate_table(discovery, old, size)
            for dependency in results:
                if old <= dependency < old + size:
                    results[dependency] = {"status": "data-match", "target_rva": target + dependency - old, "kind": "vtable"}
        except (ValueError, RuntimeError, struct.error) as error:
            results[old] = {"status": "unresolved", "reason": str(error)}
    for old in VECTOR_ICONS:
        if old not in results:
            continue
        try:
            results[old] = {"status": "data-match", "target_rva": locate_vector(discovery, old), "kind": "vector-icon"}
        except (ValueError, RuntimeError, struct.error) as error:
            results[old] = {"status": "unresolved", "reason": str(error)}
    old = 0x10E4E720
    palette = read(discovery.baseline, discovery.old_layout, old, 32)
    hits = list(find_readonly(discovery, re.escape(palette)))
    if len(hits) == 1:
        results[old] = {"status": "data-match", "target_rva": hits[0], "kind": "color-palette"}
    for old, size in ((0xF99F9D8, 4), (0x10A35CB0, 8), (0x10A35D50, 8), (0x10A35EC8, 8)):
        targets = discovery.references.get(old, set())
        if len(targets) == 1:
            target = next(iter(targets))
            if read(discovery.baseline, discovery.old_layout, old, size) == read(discovery.image, discovery.layout, target, size):
                results[old] = {"status": "data-match", "target_rva": target, "kind": "referenced-constant"}
    targets = discovery.references.get(0x10E552E0, set())
    if len(targets) == 1:
        target = next(iter(targets))
        if icon_table_equal(discovery, target):
            results[0x10E552E0] = {"status": "data-match", "target_rva": target, "kind": "icon-table"}
    discover_platform_slots(discovery, results)


def discover_platform_slots(discovery, results):
    """Validate PE-defined TLS identity and a referenced writable pointer slot."""
    # AddressOfIndex, defined by the PE TLS directory, identifies this slot
    # independently of neighboring globals or wildcard signatures.
    indices = []
    for image, layout in ((discovery.baseline, discovery.old_layout), (discovery.image, discovery.layout)):
        tls = struct.unpack_from("<I", image, layout.optional_header + 184)[0]
        index = struct.unpack_from("<Q", image, file_offset(layout, tls) + 16)[0] - image_base(image, layout)
        indices.append(index)
    if indices[0] == 0x11B3D0E0:
        results[indices[0]] = {"status": "data-match", "target_rva": indices[1], "kind": "pe-tls-index"}
    old = 0x11BD2300
    targets = discovery.references.get(old, set())
    if len(targets) == 1:
        target = next(iter(targets))
        valid = []
        for layout, rva, image in (
            (discovery.old_layout, old, discovery.baseline),
            (discovery.layout, target, discovery.image),
        ):
            valid.append(
                rva % 8 == 0
                and any(
                    section.rva <= rva
                    and rva + 8 <= section.rva + section.virtual_size
                    and struct.unpack_from("<I", image, layout.section_table + index * 40 + 36)[0] & 0x80000000
                    for index, section in enumerate(layout.sections)
                )
            )
        if all(valid):
            results[old] = {"status": "data-match", "target_rva": target, "kind": "referenced-pointer-slot"}
