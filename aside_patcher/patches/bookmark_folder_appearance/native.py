"""Render the hash-guarded native bookmark folder appearance payload."""

import json
import struct
from pathlib import Path

from ...common.exceptions import Refusal
from ...common.models import Payload
from ...common.utils import sha256
from ...patch_loader import local_asset
from ...pe import append_section, file_offset, parse_layout, relative_jump

_ADAPTER = json.loads((Path(__file__).resolve().parents[3] / "compatibility/adapters/1.0.928.2.json").read_text(encoding="utf-8"))
ORIGINAL_SHA256 = _ADAPTER["original_sha256"]
TOGGLE_SHA256 = "601914ed1154e9685c2c67116ec862eb7c8f7dd13d994e0ce920fff94d4231a8"
MANIFEST_SHA256 = _ADAPTER["payload_manifest_sha256"]
TRAMPOLINE_SIZE = 512
# name, entry RVA, verified overwrite bytes, copied register pushes, stack allocation
HOOKS = tuple(
    (hook["name"], hook["rva"], hook["prologue"], tuple(tuple(push) for push in hook["pushes"]), hook["stack_allocation"])
    for hook in _ADAPTER["bookmark_hooks"]
)


def load_payload() -> tuple[dict, bytes]:
    """Read the pinned payload and require unchanged native source files.

    Returns
    -------
    tuple of dict and bytes
        Relocation metadata and frozen code/data bytes.
    """
    directory = Path(__file__).parent
    manifest = local_asset(directory, "payload.json")
    if sha256(manifest) != MANIFEST_SHA256:
        raise Refusal("Bookmark appearance payload manifest changed.")
    metadata = json.loads(manifest)
    blob = local_asset(directory, "payload.bin")
    if metadata["format"] != 1 or sha256(blob) != metadata["sha256"]:
        raise Refusal("Bookmark appearance payload integrity check failed.")
    if len(blob) != metadata["code_size"] + metadata["data_size"]:
        raise Refusal("Bookmark appearance payload size disagrees with its metadata.")
    for name, expected in metadata["sources"].items():
        if sha256(local_asset(directory, name)) != expected:
            raise Refusal(f"Native source changed without rebuilding its payload: {name}")
    return metadata, blob


def trampolines(original: bytes, layout, base: int) -> tuple[bytearray, list[tuple[int, int, int]]]:
    """Copy verified prologues with unwind records describing only copied operations.

    Parameters
    ----------
    original : bytes
        Hash-verified image before installing these hooks.
    layout : ImageLayout
        Input image section mapping.
    base : int
        RVA reserved for trampoline code and unwind records.

    Returns
    -------
    tuple of bytearray and list of tuple
        Trampoline bytes and their runtime function records.
    """
    result = bytearray(TRAMPOLINE_SIZE)
    records = []
    for index, (_, rva, expected, pushes, allocation) in enumerate(HOOKS):
        before = bytes.fromhex(expected)
        offset = file_offset(layout, rva)
        if original[offset : offset + len(before)] != before:
            raise Refusal(f"Bookmark appearance hook prologue changed at RVA 0x{rva:X}.")
        address = base + index * 32
        copied = before + relative_jump(address + len(before), rva + len(before), 5)
        result[index * 32 : index * 32 + len(copied)] = copied
        codes = [bytes((position, register << 4)) for position, register in reversed(pushes)]
        if allocation:
            codes.insert(0, bytes((len(before), ((allocation - 8) // 8) << 4 | 2)))
        unwind = bytes((1, len(before), len(codes), 0)) + b"".join(codes) + bytes(2 * (len(codes) % 2))
        result[index * 32 + 16 : index * 32 + 16 + len(unwind)] = unwind
        records.append((address, address + len(copied), address + 16))
    return result, records


def relocation_value(kind: int, blob: bytearray, offset: int, source: int, destination: int) -> None:
    """Apply a supported COFF relocation without introducing absolute pointers.

    Parameters
    ----------
    kind : int
        AMD64 ADDR32NB or REL32 through REL32_5 relocation.
    blob : bytearray
        Owned output section contents.
    offset : int
        Relocation position within the section.
    source : int
        RVA of the relocation operand.
    destination : int
        Resolved destination RVA.
    """
    if kind == 3:
        value = destination + struct.unpack_from("<I", blob, offset)[0]
        struct.pack_into("<I", blob, offset, value)
    elif 4 <= kind <= 9:
        value = destination + struct.unpack_from("<i", blob, offset)[0] - source - 4 - (kind - 4)
        struct.pack_into("<i", blob, offset, value)
    else:
        raise Refusal(f"Unsupported bookmark payload relocation: {kind}")


def relocate(metadata: dict, buffers: dict, addresses: dict) -> None:
    """Resolve code, writable storage, vendor symbols, and trampoline references.

    Parameters
    ----------
    metadata : dict
        Hash-pinned frozen relocation records.
    buffers : dict
        Mutable code and data sections excluding trampoline bytes.
    addresses : dict
        Actual section and trampoline base RVAs in this image.
    """
    for source, entries in metadata["relocations"].items():
        for offset, kind, target, value in entries:
            destination = addresses[target] + value * (32 if target == "trampoline" else 1)
            relocation_value(kind, buffers[source], offset, addresses[source] + offset, destination)


def append_exceptions(original: bytes, layout, payload: Payload, metadata: dict, records: list) -> None:
    """Extend ordered unwind records for compiled functions and trampoline prologues.

    Parameters
    ----------
    original : bytes
        Verified input image with its original exception table.
    layout : ImageLayout
        Input image header and section mapping.
    payload : Payload
        Executable section receiving exception records.
    metadata : dict
        Compiled function unwind records relative to the payload.
    records : list
        Trampoline runtime function records.
    """
    code_base = payload.rva + TRAMPOLINE_SIZE
    records = sorted(records + [tuple(code_base + value for value in row) for row in metadata["unwind"]])
    for left, right in zip(records, records[1:]):
        if left[1] > right[0]:
            raise Refusal("Bookmark payload unwind ranges overlap.")
    rva, size = struct.unpack_from("<II", original, layout.optional_header + 136)
    offset = file_offset(layout, rva)
    previous = original[offset : offset + size]
    if size % 12 or not previous or struct.unpack_from("<I", previous, size - 12)[0] >= records[0][0]:
        raise Refusal("Original runtime function table cannot be extended in order.")
    payload.data.extend(bytes(-len(payload.data) % 4))
    payload.labels["exception_table"] = payload.rva + len(payload.data)
    payload.data.extend(previous + b"".join(struct.pack("<III", *row) for row in records))
    payload.labels["exception_size"] = size + len(records) * 12


def append_tables(original: bytes, layout, payload: Payload, metadata: dict, records: list) -> tuple[int, int]:
    """Append complete exception and GuardCF tables while preserving vendor entries.

    Parameters
    ----------
    original : bytes
        Verified input image, optionally containing the Chats toggle.
    layout : ImageLayout
        Input image header and section mapping.
    payload : Payload
        Executable section receiving the relocated native payload and tables.
    metadata : dict
        Relative unwind records and GuardCF callback offsets.
    records : list
        Additional runtime function records for verified trampoline prologues.

    Returns
    -------
    tuple of int
        File offset of the existing load config and new GuardCF table offset.
    """
    append_exceptions(original, layout, payload, metadata, records)
    code_base = payload.rva + TRAMPOLINE_SIZE
    rva = struct.unpack_from("<I", original, layout.optional_header + 192)[0]
    load = file_offset(layout, rva)
    table, count, flags = struct.unpack_from("<QQI", original, load + 128)
    stride = 4 + (flags >> 28)
    table = file_offset(layout, table - struct.unpack_from("<Q", original, layout.optional_header + 24)[0])
    previous = original[table : table + count * stride]
    callbacks = sorted(set(code_base + offset for offset in metadata["callbacks"]))
    if len(callbacks) != len(metadata["callbacks"]) or struct.unpack_from("<I", previous, (count - 1) * stride)[0] >= callbacks[0]:
        raise Refusal("GuardCF callback table cannot be extended in order.")
    table_offset = len(payload.data)
    payload.data.extend(previous + b"".join(struct.pack("<I", rva) + bytes(stride - 4) for rva in callbacks))
    payload.labels["guard_count"] = count + len(callbacks)
    return load, table_offset


def build_native_appearance(original: bytes) -> bytes:
    """Build the optional native feature only from explicitly verified input states.

    Parameters
    ----------
    original : bytes
        Vendor image or the verified native Chats-toggle builder output.

    Returns
    -------
    bytes
        Guarded image with executable payload, writable state, and native hooks.
    """
    if sha256(original) not in (ORIGINAL_SHA256, TOGGLE_SHA256):
        raise Refusal("Bookmark appearance requires the exact supported original or Chats-toggle image.")
    metadata, blob = load_payload()
    layout = parse_layout(original)
    base = struct.unpack_from("<I", original, layout.optional_header + 56)[0]
    payload = Payload(base)
    payload.data, records = trampolines(original, layout, base)
    buffers = {"code": bytearray(blob[: metadata["code_size"]]), "data": bytearray(blob[metadata["code_size"] :])}
    payload.data.extend(buffers["code"])
    load, guard_offset = append_tables(original, layout, payload, metadata, records)
    data_base = (base + len(payload.data) + 4095) & ~4095
    addresses = {"code": base + TRAMPOLINE_SIZE, "data": data_base, "native": 0, "trampoline": base}
    relocate(metadata, buffers, addresses)
    payload.data[TRAMPOLINE_SIZE : TRAMPOLINE_SIZE + metadata["code_size"]] = buffers["code"]
    image = bytearray(original)
    for name, rva, expected, _, _ in HOOKS:
        offset = file_offset(layout, rva)
        image[offset : offset + len(bytes.fromhex(expected))] = relative_jump(
            rva, addresses["code"] + metadata["symbols"]["Bfa" + name + "Hook"], len(bytes.fromhex(expected))
        )
    struct.pack_into("<II", image, layout.optional_header + 136, payload.labels["exception_table"], payload.labels["exception_size"])
    imagebase = struct.unpack_from("<Q", original, layout.optional_header + 24)[0]
    # The load-config VA field already has its original loader relocation.
    struct.pack_into("<QQ", image, load + 128, imagebase + base + guard_offset, payload.labels["guard_count"])
    struct.pack_into("<I", image, layout.optional_header + 64, 0)
    result = append_section(image, layout, payload, name=b".bmcode")
    return append_section(
        bytearray(result), parse_layout(result), Payload(data_base, buffers["data"]), name=b".bmdata", characteristics=0xC0000040
    )
