"""Extend the Chats preference bridge and install verified native detours."""

import json
import struct

from ...common.models import ImageLayout, Payload
from ...pe import relative_jump, replace_at_rva
from .constants import CHATS_PREFERENCE, IMAGE_BASE


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
