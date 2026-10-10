"""Render the verified native Chats settings-toggle patch."""

from ...common.exceptions import Refusal
from ...common.models import Payload
from ...common.utils import sha256
from ...pe import append_section, append_unwind_table, parse_layout, resolve_blocks
from .bridge import append_schema, install_hooks
from .constants import ORIGINAL_DLL_SHA256
from .instructions import emit_observer, emit_preference_bridge, emit_registration, emit_visibility


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
    if sha256(original) != ORIGINAL_DLL_SHA256:
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
