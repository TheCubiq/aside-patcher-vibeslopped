"""Native Chats toggle recipe for the exact verified Aside 1.0.928.2 DLL."""

import struct

from .common.models import CHATS_PREFERENCE, Payload


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
            "488907 48c74708" + struct.pack("<I", len(CHATS_PREFERENCE)).hex(),
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
            "488d542428 48890a 48c74208" + struct.pack("<I", len(CHATS_PREFERENCE)).hex(),
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
        code.emit("41ba" + struct.pack("<I", len(CHATS_PREFERENCE)).hex())
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
        code.emit("4889442420 48c7442428" + struct.pack("<I", len(CHATS_PREFERENCE)).hex() + "488d542420")
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
