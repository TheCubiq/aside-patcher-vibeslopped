"""Native Chats toggle recipe for the exact verified Aside 1.0.928.2 DLL."""

import hashlib
import json
import struct

from .core import Refusal

PREF = b"aside.vertical_tabs.chats_section_enabled"
BASE = 0x180000000


class Code:
    def __init__(self, rva):
        self.rva = rva
        self.data = bytearray()
        self.labels = {}
        self.fixups = []

    def emit(self, hexbytes):
        self.data.extend(bytes.fromhex(hexbytes))

    def label(self, name):
        self.labels[name] = self.rva + len(self.data)

    def rel(self, op, dest):
        self.emit(op)
        self.fixups.append((len(self.data), dest))
        self.data.extend(b"\0" * 4)

    def finish(self, labels):
        for off, dest in self.fixups:
            if isinstance(dest, str):
                dest = labels[dest]
            struct.pack_into("<i", self.data, off, dest - (self.rva + off + 4))
        return bytes(self.data)


# The verified machine-code recipe stays in execution order for auditing.
# pylint: disable=too-many-locals,too-many-branches,too-many-statements
def build_native_toggle(original: bytes) -> bytes:
    if (
        hashlib.sha256(original).hexdigest()
        != "9cdc4b08d095abb48fdfec2babf7be54ed35eb67ec20af9341d4cf5302e5d510"
    ):
        raise Refusal("Native toggle recipe requires the exact original DLL.")
    d = bytearray(original)
    pe = struct.unpack_from("<I", d, 60)[0]
    n = struct.unpack_from("<H", d, pe + 6)[0]
    opt = pe + 24
    sh = opt + struct.unpack_from("<H", d, pe + 20)[0]
    sections = []
    for i in range(n):
        vs, rva, size, offset = struct.unpack_from("<IIII", d, sh + i * 40 + 8)
        sections.append((rva, size, offset, vs))

    def fileoff(rva):
        for r, size, off, _virtual_size in sections:
            if r <= rva < r + size:
                return off + rva - r
        raise ValueError(hex(rva))

    patches = []

    def patch(rva, after):
        off = fileoff(rva)
        before = original[off : off + len(after)]
        d[off : off + len(after)] = after
        patches.append({"offset": off, "before": before.hex(), "after": after.hex()})

    new_rva = (max(r + vs for r, size, off, vs in sections) + 4095) & ~4095
    new_off = (len(d) + 511) & ~511
    assert sh + (n + 1) * 40 <= min(off for r, size, off, vs in sections)
    payload = bytearray()
    blocks = []
    labels = {}

    def block(name):
        while len(payload) % 16:
            payload.append(0x90)
        c = Code(new_rva + len(payload))
        labels[name] = c.rva
        blocks.append(c)
        return c

    def done(c):
        labels.update(c.labels)
        payload.extend(c.data)

    # Register one additional false-default boolean using the native registry.
    c = block("register")
    c.emit("c60300 c6431801")
    c.rel("488d05", "pref")
    c.emit("488907 48c74708" + struct.pack("<I", len(PREF)).hex())
    c.emit("896c2420 4889f1 4889fa 4989d8 4531c9")
    c.rel("e8", 0x4CAFF90)
    c.emit("448833 44887318")
    c.rel("e9", 0x24FC446)
    done(c)
    # Add the same refresh callback under the independent Chats preference.
    c = block("observe")
    c.emit("b938000000")
    c.rel("e8", 0x5DF7BE0)
    c.emit("4889c3 c70001000000 4c896008 4c896810 4c897818")
    c.rel("488d05", 0x36E4460)
    c.emit("48894320 c7432800000000 48897b30 b930000000")
    c.rel("e8", 0x5DF7BE0)
    c.emit("c70001000000")
    c.rel("488d0d", 0x18E22E0)
    c.emit("48894808")
    c.rel("488d0d", 0x2C7F6B0)
    c.emit("48894810 4c897818 48895828")
    c.rel("488d0d", "pref")
    c.emit("488d542428 48890a 48c74208" + struct.pack("<I", len(PREF)).hex())
    c.emit("4889f1 4989c0")
    c.rel("e8", 0x68BED0)
    c.rel("4c3b35", 0x119E9CC8)
    c.rel("e9", 0x36B73F9)
    done(c)
    # Optional positional boolean selects Chats; existing calls remain Bookmarks.
    for name, count, valueoff, back in [
        ("get", 1, 0, 0xA210CC8),
        ("set", 2, 0x20, 0x35FB890),
    ]:
        c = block(name)
        c.rel("488d0d", 0x1070D6E8)
        c.emit("41ba2d000000")
        c.emit("48837f20" + bytes([count]).hex())
        c.rel("0f82", name + "_end")
        c.emit("4c8b5f18 41807b" + bytes([valueoff + 0x18]).hex() + "01")
        c.rel("0f85", name + "_end")
        c.emit("41807b" + bytes([valueoff]).hex() + "00")
        c.rel("0f84", name + "_end")
        c.rel("488d0d", "pref")
        c.emit("41ba" + struct.pack("<I", len(PREF)).hex())
        c.label(name + "_end")
        c.rel("e9", back)
        done(c)
    # Read the boolean while preserving the surrounding function's registers.
    for name, back in [("visible1", 0x36B6C36), ("visible2", 0x36D27F9)]:
        c = block(name)
        if name == "visible2":
            c.emit("89da")
        c.emit("50 51 52 4150 4151 4152 4153 4883ec48")
        c.emit("488b8e68040000 4885c9")
        c.rel("0f84", name + "_false")
        c.emit("488b8948050000 4885c9")
        c.rel("0f84", name + "_false")
        c.emit("488b4968 4885c9")
        c.rel("0f84", name + "_false")
        c.emit("488b01 488b8048010000")
        c.rel("ff15", 0x1191EAA0)
        c.emit("4885c0")
        c.rel("0f84", name + "_false")
        c.emit("4889c1 488b00 488b8068010000")
        c.rel("ff15", 0x1191EAA0)
        c.emit("4885c0")
        c.rel("0f84", name + "_false")
        c.emit("4889c1")
        c.rel("488d05", "pref")
        c.emit(
            "4889442420 48c7442428" + struct.pack("<I", len(PREF)).hex() + "488d542420"
        )
        c.rel("e8", 0x5136990)
        c.rel("e9", name + "_combine")
        c.label(name + "_false")
        c.emit("31c0")
        c.label(name + "_combine")
        c.emit("20442468 4883c448 415b 415a 4159 4158 5a 59 58")
        if name == "visible1":
            c.emit("488b8ef0050000")
        else:
            c.emit("488b01 488b8090000000")
        c.rel("e9", back)
        done(c)
    labels["pref"] = new_rva + len(payload)
    payload.extend(PREF + b"\0")
    schema_start = (
        original.rfind(
            b"\0", 0, original.find(b"getVerticalTabsBookmarksSectionEnabled")
        )
        + 1
    )
    schema_end = original.find(b"\0", schema_start)
    schema = json.loads(original[schema_start:schema_end])
    for f in schema["functions"]:
        if f["name"] in (
            "getVerticalTabsBookmarksSectionEnabled",
            "setVerticalTabsBookmarksSectionEnabled",
        ):
            f["parameters"].append(
                {"name": "chats", "type": "boolean", "optional": True}
            )
    new_schema = json.dumps(schema, separators=(",", ":")).encode()
    labels["schema"] = new_rva + len(payload)
    payload.extend(new_schema + b"\0")
    # Describe detour frames to Windows' unwinder, chaining to original frames.
    exception_rva, exception_size = struct.unpack_from("<II", original, opt + 136)
    exception_off = fileoff(exception_rva)
    parents = {
        "register": 0x24FC090,
        "observe": 0x36B7250,
        "get": 0xA210C00,
        "set": 0x35FB6B0,
        "visible1": 0x36B6B00,
        "visible2": 0x36D1E20,
    }
    entries = [
        struct.unpack_from("<III", original, exception_off + i)
        for i in range(0, exception_size, 12)
    ]
    new_entries = []
    for c in blocks:
        name = next(name for name in parents if labels[name] == c.rva)
        parent = next(e for e in entries if e[0] == parents[name])
        while len(payload) % 4:
            payload.append(0)
        unwind_rva = new_rva + len(payload)
        if name.startswith("visible"):
            bias = 2 if name == "visible2" else 0
            offsets = [15, 11, 9, 7, 5, 3, 2, 1]
            payload.extend(bytes([0x21, 15 + bias, 8, 0]))
            for i, offset in enumerate(offsets):
                payload.extend(bytes([offset + bias, 0x82 if i == 0 else 0x02]))
        else:
            payload.extend(bytes([0x21, 0, 0, 0]))
        payload.extend(struct.pack("<III", *parent))
        new_entries.append((c.rva, c.rva + len(c.data), unwind_rva))
    extra = b"".join(struct.pack("<III", *e) for e in sorted(new_entries))
    assert not any(
        original[
            exception_off + exception_size : exception_off + exception_size + len(extra)
        ]
    )
    d[exception_off + exception_size : exception_off + exception_size + len(extra)] = (
        extra
    )
    struct.pack_into("<I", d, opt + 140, exception_size + len(extra))
    for i in range(n):
        if sections[i][0] == exception_rva:
            struct.pack_into(
                "<I",
                d,
                sh + i * 40 + 8,
                max(sections[i][3], exception_size + len(extra)),
            )
    for c in blocks:
        data = c.finish(labels)
        off = c.rva - new_rva
        payload[off : off + len(data)] = data

    def hook(rva, name, size):
        patch(
            rva,
            b"\xe9" + struct.pack("<i", labels[name] - rva - 5) + b"\x90" * (size - 5),
        )

    hook(0x24FC43F, "register", 7)
    hook(0x36B73F2, "observe", 7)
    hook(0xA210CC1, "get", 7)
    patch(0xA210CD0, bytes.fromhex("4c89520890909090"))
    hook(0x35FB889, "set", 7)
    patch(0x35FB898, bytes.fromhex("4c89520890909090"))
    hook(0x36B6C2F, "visible1", 7)
    hook(0x36D27EF, "visible2", 10)
    patch(0x36D27F9, b"\x90\x90")
    # Existing schema table relocation already rebases this pointer.
    schema_table_off = 0x10DD1350
    assert (
        struct.unpack_from("<Q", d, schema_table_off)[0] == BASE + schema_start + 0x1000
    )
    d[schema_table_off : schema_table_off + 16] = struct.pack(
        "<QQ", BASE + labels["schema"], len(new_schema)
    )
    raw_size = (len(payload) + 511) & ~511
    new_header = struct.pack(
        "<8sIIIIIIHHI",
        b".chatmod",
        len(payload),
        new_rva,
        raw_size,
        new_off,
        0,
        0,
        0,
        0,
        0x60000020,
    )
    d[sh + n * 40 : sh + (n + 1) * 40] = new_header
    struct.pack_into("<H", d, pe + 6, n + 1)
    struct.pack_into("<I", d, opt + 56, (new_rva + len(payload) + 4095) & ~4095)
    struct.pack_into(
        "<I", d, opt + 4, struct.unpack_from("<I", d, opt + 4)[0] + raw_size
    )
    d.extend(b"\0" * (new_off - len(d)))
    d.extend(payload)
    d.extend(b"\0" * (raw_size - len(payload)))
    return bytes(d)
