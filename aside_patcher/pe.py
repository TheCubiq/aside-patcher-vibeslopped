"""Map PE sections, install detours, and extend exception and section tables."""

import json
import struct

from .common.exceptions import Refusal
from .common.models import CHATS_PREFERENCE, IMAGE_BASE, ImageLayout, Payload, Section


def parse_layout(original: bytes) -> ImageLayout:
    """Read PE header offsets and original section bounds.

    Parameters
    ----------
    original : bytes
        Verified original DLL contents.

    Returns
    -------
    ImageLayout
        Original image section mapping and header locations.
    """
    pe_header = struct.unpack_from("<I", original, 60)[0]
    count = struct.unpack_from("<H", original, pe_header + 6)[0]
    optional_header = pe_header + 24
    section_table = optional_header + struct.unpack_from("<H", original, pe_header + 20)[0]
    sections = []
    for index in range(count):
        virtual_size, rva, raw_size, offset = struct.unpack_from("<IIII", original, section_table + index * 40 + 8)
        sections.append(Section(rva, raw_size, offset, virtual_size))
    return ImageLayout(pe_header, optional_header, section_table, tuple(sections))


def file_offset(layout: ImageLayout, rva: int) -> int:
    """Translate an RVA through the original PE section mapping.

    Parameters
    ----------
    layout : ImageLayout
        Original PE header offsets and section mappings.
    rva : int
        Relative virtual address within the original image.

    Returns
    -------
    int
        Physical byte offset corresponding to the RVA.
    """
    for section in layout.sections:
        if section.rva <= rva < section.rva + section.raw_size:
            return section.file_offset + rva - section.rva
    raise Refusal(f"RVA 0x{rva:X} is outside mapped sections.")


def replace_at_rva(data: bytearray, layout: ImageLayout, rva: int, replacement: bytes) -> None:
    """Replace bytes at an original mapped instruction address.

    Parameters
    ----------
    data : bytearray
        Mutable DLL contents, or the binary bytes under validation.
    layout : ImageLayout
        Original PE header offsets and section mappings.
    rva : int
        Relative virtual address within the original image.
    replacement : bytes
        Exact replacement instruction bytes.
    """
    offset = file_offset(layout, rva)
    data[offset : offset + len(replacement)] = replacement


def relative_jump(source: int, destination: int, size: int) -> bytes:
    """Encode a relative jump padded to the verified overwrite span.

    Parameters
    ----------
    source : int
        Relative virtual address of the jump instruction.
    destination : int
        Relative virtual address of the target block.
    size : int
        Verified overwrite span in bytes, at least five.

    Returns
    -------
    bytes
        Jump instruction followed by NOP padding.
    """
    return b"\xe9" + struct.pack("<i", destination - source - 5) + b"\x90" * (size - 5)


def unwind_bytes(name: str) -> bytes:
    """Encode the extra unwind operations for a native detour frame.

    Parameters
    ----------
    name : str
        Symbolic hook or block name.

    Returns
    -------
    bytes
        Windows unwind header and extra detour frame operations.
    """
    if not name.startswith("visible"):
        return bytes([0x21, 0, 0, 0])
    bias = 2 if name == "visible2" else 0
    offsets = [15, 11, 9, 7, 5, 3, 2, 1]
    info = bytearray([0x21, 15 + bias, 8, 0])
    for index, offset in enumerate(offsets):
        info.extend(bytes([offset + bias, 0x82 if index == 0 else 0x02]))
    return bytes(info)


def append_unwind_table(original: bytes, data: bytearray, layout: ImageLayout, payload: Payload) -> None:
    # Describe detour frames to Windows' unwinder, chaining to original frames.
    """Chain detour unwind records to the original runtime functions.

    Parameters
    ----------
    original : bytes
        Verified original DLL contents.
    data : bytearray
        Mutable DLL contents, or the binary bytes under validation.
    layout : ImageLayout
        Original PE header offsets and section mappings.
    payload : Payload
        New section contents, hook blocks, and shared symbolic addresses.
    """
    exception_rva, exception_size = struct.unpack_from("<II", original, layout.optional_header + 136)
    exception_off = file_offset(layout, exception_rva)
    parents = {
        "register": 0x24FC090,
        "observe": 0x36B7250,
        "get": 0xA210C00,
        "set": 0x35FB6B0,
        "visible1": 0x36B6B00,
        "visible2": 0x36D1E20,
    }
    entries = {entry[0]: entry for entry in struct.iter_unpack("<III", original[exception_off : exception_off + exception_size])}
    names_by_address = {payload.labels[name]: name for name in parents}
    new_entries = []
    for code in payload.blocks:
        name = names_by_address[code.rva]
        parent = entries[parents[name]]
        payload.data.extend(b"\0" * (-len(payload.data) % 4))
        unwind_rva = payload.rva + len(payload.data)
        payload.data.extend(unwind_bytes(name))
        payload.data.extend(struct.pack("<III", *parent))
        new_entries.append((code.rva, code.rva + len(code.data), unwind_rva))
    extra = b"".join(struct.pack("<III", *e) for e in sorted(new_entries))
    assert not any(original[exception_off + exception_size : exception_off + exception_size + len(extra)])
    data[exception_off + exception_size : exception_off + exception_size + len(extra)] = extra
    struct.pack_into("<I", data, layout.optional_header + 140, exception_size + len(extra))
    for i, section in enumerate(layout.sections):
        if section.rva == exception_rva:
            struct.pack_into(
                "<I",
                data,
                layout.section_table + i * 40 + 8,
                max(section.virtual_size, exception_size + len(extra)),
            )


def resolve_blocks(payload: Payload) -> None:
    """Resolve deferred operands and copy finalized instructions into the payload.

    Parameters
    ----------
    payload : Payload
        New section contents, hook blocks, and shared symbolic addresses.
    """
    for code in payload.blocks:
        data = code.finish(payload.labels)
        offset = code.rva - payload.rva
        payload.data[offset : offset + len(data)] = data


def append_section(data: bytearray, layout: ImageLayout, payload: Payload) -> bytes:
    """Append the executable hook section and update PE header sizes.

    Parameters
    ----------
    data : bytearray
        Mutable DLL contents, or the binary bytes under validation.
    layout : ImageLayout
        Original PE header offsets and section mappings.
    payload : Payload
        New section contents, hook blocks, and shared symbolic addresses.

    Returns
    -------
    bytes
        Final DLL contents containing the new executable section.
    """
    count = len(layout.sections)
    new_offset = (len(data) + 511) & ~511
    raw_size = (len(payload.data) + 511) & ~511
    new_header = struct.pack(
        "<8sIIIIIIHHI",
        b".chatmod",
        len(payload.data),
        payload.rva,
        raw_size,
        new_offset,
        0,
        0,
        0,
        0,
        0x60000020,
    )
    data[layout.section_table + count * 40 : layout.section_table + (count + 1) * 40] = new_header
    struct.pack_into("<H", data, layout.pe_header + 6, count + 1)
    struct.pack_into("<I", data, layout.optional_header + 56, (payload.rva + len(payload.data) + 4095) & ~4095)
    struct.pack_into("<I", data, layout.optional_header + 4, struct.unpack_from("<I", data, layout.optional_header + 4)[0] + raw_size)
    data.extend(b"\0" * (new_offset - len(data)))
    data.extend(payload.data)
    data.extend(b"\0" * (raw_size - len(payload.data)))
    return bytes(data)


def append_schema(original: bytes, payload: Payload) -> tuple[int, int]:
    """Append the preference key and extended native bridge schema.

    Parameters
    ----------
    original : bytes
        Verified original DLL contents.
    payload : Payload
        New section contents, hook blocks, and shared symbolic addresses.

    Returns
    -------
    tuple[int, int]
        Original schema offset and replacement schema byte length.
    """
    payload.labels["pref"] = payload.rva + len(payload.data)
    payload.data.extend(CHATS_PREFERENCE + b"\0")
    schema_start = original.rfind(b"\0", 0, original.find(b"getVerticalTabsBookmarksSectionEnabled")) + 1
    schema_end = original.find(b"\0", schema_start)
    schema = json.loads(original[schema_start:schema_end])
    for function in schema["functions"]:
        if function["name"] in (
            "getVerticalTabsBookmarksSectionEnabled",
            "setVerticalTabsBookmarksSectionEnabled",
        ):
            function["parameters"].append({"name": "chats", "type": "boolean", "optional": True})
    new_schema = json.dumps(schema, separators=(",", ":")).encode()
    payload.labels["schema"] = payload.rva + len(payload.data)
    payload.data.extend(new_schema + b"\0")
    return schema_start, len(new_schema)


def install_hooks(data: bytearray, layout: ImageLayout, payload: Payload, schema_start: int, schema_size: int) -> None:
    """Install verified instruction detours and replace the bridge schema pointer.

    Parameters
    ----------
    data : bytearray
        Mutable DLL contents, or the binary bytes under validation.
    layout : ImageLayout
        Original PE header offsets and section mappings.
    payload : Payload
        New section contents, hook blocks, and shared symbolic addresses.
    schema_start : int
        Original bridge schema offset used to verify its relocated pointer.
    schema_size : int
        Length of the replacement JSON schema, excluding its terminator.
    """
    jump_sites = {
        "register": 0x24FC43F,
        "observe": 0x36B73F2,
        "get": 0xA210CC1,
        "set": 0x35FB889,
        "visible1": 0x36B6C2F,
        "visible2": 0x36D27EF,
    }
    for name, rva in jump_sites.items():
        # All verified overwrite spans are seven bytes except the second visibility hook.
        size = 10 if name == "visible2" else 7
        replace_at_rva(data, layout, rva, relative_jump(rva, payload.labels[name], size))
    for rva in (0xA210CD0, 0x35FB898):
        replace_at_rva(data, layout, rva, bytes.fromhex("4c89520890909090"))
    replace_at_rva(data, layout, 0x36D27F9, b"\x90\x90")
    # Existing schema table relocation already rebases this pointer.
    schema_table_off = 0x10DD1350
    assert struct.unpack_from("<Q", data, schema_table_off)[0] == IMAGE_BASE + schema_start + 0x1000
    data[schema_table_off : schema_table_off + 16] = struct.pack("<QQ", IMAGE_BASE + payload.labels["schema"], schema_size)
