"""Native Chats toggle recipe for the exact verified Aside 1.0.928.2 DLL."""

import hashlib
import json
import struct
from dataclasses import dataclass, field

from .core import Refusal

PREF = b"aside.vertical_tabs.chats_section_enabled"
BASE = 0x180000000


class Code:
    """Emit x64 instructions and resolve relative address fixups.

    Parameters
    ----------
    rva : int
        Relative virtual address of the first emitted instruction.
    """

    def __init__(self, rva: int) -> None:
        self.rva = rva
        self.data = bytearray()
        self.labels = {}
        self.fixups = []

    def emit(self, instruction_hex: str) -> None:
        """Append machine-code bytes written as hexadecimal text.

        Parameters
        ----------
        instruction_hex : str
            Hexadecimal instruction bytes.
        """
        self.data.extend(bytes.fromhex(instruction_hex))

    def label(self, name: str) -> None:
        """Record the current instruction address under a symbolic name.

        Parameters
        ----------
        name : str
            Symbolic instruction label.
        """
        self.labels[name] = self.rva + len(self.data)

    def rel(self, opcode: str, destination: int | str) -> None:
        """Emit an instruction with a deferred relative-address operand.

        Parameters
        ----------
        opcode : str
            Hexadecimal opcode preceding a signed 32-bit displacement.
        destination : int or str
            Destination RVA or symbolic label.
        """
        self.emit(opcode)
        self.fixups.append((len(self.data), destination))
        self.data.extend(b"\0" * 4)

    def sequence(self, instructions: tuple[str | tuple[str, int | str], ...]) -> None:
        """Emit an ordered listing of raw instructions and relative-address operands.

        Parameters
        ----------
        instructions : tuple[str or tuple[str, int or str], ...]
            Hexadecimal byte strings, or opcode/destination pairs with deferred fixups.
        """
        for instruction in instructions:
            if isinstance(instruction, str):
                self.emit(instruction)
            else:
                self.rel(*instruction)

    def finish(self, labels: dict[str, int]) -> bytes:
        """Resolve all relative operands and return immutable machine code.

        Parameters
        ----------
        labels : dict of str to int
            Resolved label RVAs.

        Returns
        -------
        bytes
            Machine code with resolved relative displacements.
        """
        for offset, destination in self.fixups:
            if isinstance(destination, str):
                destination = labels[destination]
            struct.pack_into("<i", self.data, offset, destination - (self.rva + offset + 4))
        return bytes(self.data)


@dataclass(frozen=True)
class Section:
    """Record virtual and physical bounds of one PE section."""

    rva: int
    raw_size: int
    file_offset: int
    virtual_size: int


@dataclass(frozen=True)
class ImageLayout:
    """Record the original PE headers and section mapping."""

    pe_header: int
    optional_header: int
    section_table: int
    sections: tuple[Section, ...]


@dataclass
class Payload:
    """Assemble aligned hook blocks and their shared data.

    Attributes
    ----------
    rva : int
        Relative virtual address of the new section.
    data : bytearray
        Section contents in block order.
    blocks : list of Code
        Emitters awaiting relative-address resolution.
    labels : dict of str to int
        Instruction and shared-data RVAs by name.
    """

    rva: int
    data: bytearray = field(default_factory=bytearray)
    blocks: list[Code] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)

    def start(self, name: str) -> Code:
        """Start an aligned hook block and register its symbolic address.

        Parameters
        ----------
        name : str
            Symbolic hook or block name.

        Returns
        -------
        Code
            Emitter for the new aligned block.
        """
        self.data.extend(b"\x90" * (-len(self.data) % 16))
        code = Code(self.rva + len(self.data))
        self.labels[name] = code.rva
        self.blocks.append(code)
        return code

    def append(self, code: Code) -> None:
        """Append a completed hook block and merge its internal labels.

        Parameters
        ----------
        code : Code
            Completed emitter with instruction bytes and internal labels.
        """
        self.labels.update(code.labels)
        self.data.extend(code.data)


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


def emit_registration(payload: Payload) -> None:
    # Register one additional false-default boolean using the native registry.
    """Emit the false-default Chats preference registration hook.

    Parameters
    ----------
    payload : Payload
        New section contents, hook blocks, and shared symbolic addresses.
    """
    code = payload.start("register")
    code.sequence(
        (
            "c60300 c6431801",
            ("488d05", "pref"),
            "488907 48c74708" + struct.pack("<I", len(PREF)).hex(),
            "896c2420 4889f1 4889fa 4989d8 4531c9",
            ("e8", 0x4CAFF90),
            "448833 44887318",
            ("e9", 0x24FC446),
        )
    )
    payload.append(code)


def emit_observer(payload: Payload) -> None:
    # Add the same refresh callback under the independent Chats preference.
    """Emit the Chats preference observer using the existing refresh callback.

    Parameters
    ----------
    payload : Payload
        New section contents, hook blocks, and shared symbolic addresses.
    """
    code = payload.start("observe")
    code.sequence(
        (
            "b938000000",
            ("e8", 0x5DF7BE0),
            "4889c3 c70001000000 4c896008 4c896810 4c897818",
            ("488d05", 0x36E4460),
            "48894320 c7432800000000 48897b30 b930000000",
            ("e8", 0x5DF7BE0),
            "c70001000000",
            ("488d0d", 0x18E22E0),
            "48894808",
            ("488d0d", 0x2C7F6B0),
            "48894810 4c897818 48895828",
            ("488d0d", "pref"),
            "488d542428 48890a 48c74208" + struct.pack("<I", len(PREF)).hex(),
            "4889f1 4989c0",
            ("e8", 0x68BED0),
            ("4c3b35", 0x119E9CC8),
            ("e9", 0x36B73F9),
        )
    )
    payload.append(code)


def emit_preference_bridge(payload: Payload) -> None:
    # Optional positional boolean selects Chats; existing calls remain Bookmarks.
    """Emit getter and setter hooks selecting Chats or Bookmarks.

    Parameters
    ----------
    payload : Payload
        New section contents, hook blocks, and shared symbolic addresses.
    """
    for name, count, argument_offset, return_rva in [
        ("get", 1, 0, 0xA210CC8),
        ("set", 2, 0x20, 0x35FB890),
    ]:
        code = payload.start(name)
        code.rel("488d0d", 0x1070D6E8)
        code.emit("41ba2d000000")
        code.emit("48837f20" + bytes([count]).hex())
        code.rel("0f82", name + "_end")
        code.emit("4c8b5f18 41807b" + bytes([argument_offset + 0x18]).hex() + "01")
        code.rel("0f85", name + "_end")
        code.emit("41807b" + bytes([argument_offset]).hex() + "00")
        code.rel("0f84", name + "_end")
        code.rel("488d0d", "pref")
        code.emit("41ba" + struct.pack("<I", len(PREF)).hex())
        code.label(name + "_end")
        code.rel("e9", return_rva)
        payload.append(code)


def emit_visibility(payload: Payload) -> None:
    # Read the boolean while preserving the surrounding function's registers.
    """Emit both preference-controlled Chats visibility hooks.

    Parameters
    ----------
    payload : Payload
        New section contents, hook blocks, and shared symbolic addresses.
    """
    for name, return_rva in [("visible1", 0x36B6C36), ("visible2", 0x36D27F9)]:
        code = payload.start(name)
        if name == "visible2":
            code.emit("89da")
        code.emit("50 51 52 4150 4151 4152 4153 4883ec48")
        for instruction in ("488b8e68040000 4885c9", "488b8948050000 4885c9", "488b4968 4885c9"):
            code.emit(instruction)
            code.rel("0f84", name + "_false")
        for instruction in ("488b01 488b8048010000", "4889c1 488b00 488b8068010000"):
            code.emit(instruction)
            code.rel("ff15", 0x1191EAA0)
            code.emit("4885c0")
            code.rel("0f84", name + "_false")
        code.emit("4889c1")
        code.rel("488d05", "pref")
        code.emit("4889442420 48c7442428" + struct.pack("<I", len(PREF)).hex() + "488d542420")
        code.rel("e8", 0x5136990)
        code.rel("e9", name + "_combine")
        code.label(name + "_false")
        code.emit("31c0")
        code.label(name + "_combine")
        code.emit("20442468 4883c448 415b 415a 4159 4158 5a 59 58")
        if name == "visible1":
            code.emit("488b8ef0050000")
        else:
            code.emit("488b01 488b8090000000")
        code.rel("e9", return_rva)
        payload.append(code)


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
    payload.data.extend(PREF + b"\0")
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
    assert struct.unpack_from("<Q", data, schema_table_off)[0] == BASE + schema_start + 0x1000
    data[schema_table_off : schema_table_off + 16] = struct.pack("<QQ", BASE + payload.labels["schema"], schema_size)


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
    if hashlib.sha256(original).hexdigest() != "9cdc4b08d095abb48fdfec2babf7be54ed35eb67ec20af9341d4cf5302e5d510":
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
