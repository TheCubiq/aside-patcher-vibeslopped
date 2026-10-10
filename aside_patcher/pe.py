"""Map PE sections, install detours, and extend exception and section tables."""

import struct

from .common.exceptions import Refusal
from .common.models import ImageLayout, Payload, Section


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
