"""Freeze MSVC COFF into portable bytes and relocation metadata for the patcher."""

from pathlib import Path
import argparse, hashlib, json, struct

BINDINGS = {
    name: ("native", rva)
    for name, rva in {
        "BfaImageBase": 0,
        "__ImageBase": 0,
        "BfaNativeIcons": 0x10E552E0,
        "BfaNativeColors": 0x10E4E720,
        "BfaNativeTlsIndex": 0x11B3D0E0,
        "BfaFolderIcon": 0x10C6A378,
        "BfaCaretIcon": 0x10C6AB38,
        "BfaRowCornerRadius": 0xF99F9D8,
        "memset": 0x5E5D620,
    }.items()
}
for index, name in enumerate(
    ("OwnerConstructor", "OwnerDestructor", "RowIcon", "RowDestructor", "FolderMenu", "ModelRemove", "ViewBackground", "RowBackground", "RowBorder", "RowMask", "RowsPaint", "LayoutImmediately", "FolderToggle", "AnimationProgress", "RowAnimationProgress", "PreferredSize")
):
    BINDINGS["BfaOriginal" + name] = ("trampoline", index)


def aligned(value: int, boundary: int) -> int:
    return (value + boundary - 1) & -boundary


class CoffObject:
    def __init__(self, path):
        data = path.read_bytes()
        machine, count, _, symbol_offset, symbol_count, optional, _ = struct.unpack_from("<HHIIIHH", data)
        if machine != 0x8664 or optional:
            raise ValueError("Only AMD64 MSVC COFF objects are supported")
        strings = data[symbol_offset + symbol_count * 18 :]

        def name(raw: bytes) -> str:
            if raw[:4] == bytes(4):
                pos = struct.unpack_from("<I", raw, 4)[0]
            elif raw[:1] == b"/":
                pos = int(raw[1:].split(b"\0")[0])
            else:
                return raw.split(b"\0")[0].decode()
            return strings[pos : strings.index(b"\0", pos)].decode()

        self.sections = []
        for index in range(count):
            raw = data[20 + index * 40 : 60 + index * 40]
            size, ptr, relptr, _, relcount, _, flags = struct.unpack_from("<IIIIHHI", raw, 16)
            self.sections.append(
                {
                    "name": name(raw[:8]),
                    "bytes": bytearray(data[ptr : ptr + size] if ptr else bytes(size)),
                    "relocations": [struct.unpack_from("<IIH", data, relptr + i * 10) for i in range(relcount)],
                    "flags": flags,
                }
            )
        self.symbols = [None] * symbol_count
        index = 0
        while index < symbol_count:
            raw, value, section, _, storage, aux = struct.unpack_from("<8sIhHBB", data, symbol_offset + index * 18)
            self.symbols[index] = {"name": name(raw), "value": value, "section": section, "storage": storage}
            index += aux + 1


def collect_definitions(obj: CoffObject, definitions: dict) -> None:
    for symbol in obj.symbols:
        if not symbol or symbol["storage"] != 2 or symbol["section"] <= 0:
            continue
        section = obj.sections[symbol["section"] - 1]
        if "region" not in section:
            continue
        target = (section["region"], section["offset"] + symbol["value"])
        if symbol["name"] in definitions:
            if not symbol["name"].startswith("??_C@"):
                raise ValueError("Duplicate external definition: " + symbol["name"])
        else:
            definitions[symbol["name"]] = target


def allocate_sections(objects: list[CoffObject]) -> tuple[dict, dict]:
    blobs = {"code": bytearray(), "data": bytearray()}
    definitions = {}
    for obj in objects:
        for section in obj.sections:
            flags = section["flags"]
            name = section["name"]
            if not flags & 0xE0 or name.startswith(".debug") or name in (".gfids$y", ".giats$y", ".gljmp$y", ".gehcont$y"):
                continue
            region = "data" if flags & 0x80000000 else "code"
            blob = blobs[region]
            offset = aligned(len(blob), 1 << max(0, ((flags >> 20) & 15) - 1))
            blob.extend(bytes(offset - len(blob)))
            blob.extend(section["bytes"])
            section.update(region=region, offset=offset)
        collect_definitions(obj, definitions)
    return blobs, definitions


def symbol_target(obj: CoffObject, index: int, definitions: dict) -> tuple[str, int]:
    symbol = obj.symbols[index]
    if symbol["section"] > 0:
        section = obj.sections[symbol["section"] - 1]
        return section["region"], section["offset"] + symbol["value"]
    if symbol["section"] == -1:
        return "native", symbol["value"]
    return definitions[symbol["name"]] if symbol["name"] in definitions else BINDINGS[symbol["name"]]


def section_relocations(obj: CoffObject, section: dict, definitions: dict, relocations: dict) -> dict:
    offsets = {}
    for offset, index, kind in section["relocations"]:
        if kind != 3 and not 4 <= kind <= 9:
            raise ValueError("Unsupported relocation type: " + str(kind))
        region, value = symbol_target(obj, index, definitions)
        relocations[section["region"]].append([section["offset"] + offset, kind, region, value])
        offsets[offset] = (region, value)
    return offsets


def unwind_records(section: dict, offsets: dict) -> list:
    result = []
    for offset in range(0, len(section["bytes"]), 12):
        row = []
        for field in (0, 4, 8):
            region, value = offsets[offset + field]
            if region != "code":
                raise ValueError("Unwind address is outside code")
            row.append(value + struct.unpack_from("<I", section["bytes"], offset + field)[0])
        result.append(row)
    return result


def guard_callbacks(obj: CoffObject, section: dict, definitions: dict) -> set[int]:
    callbacks = set()
    for (index,) in struct.iter_unpack("<I", section["bytes"]):
        region, offset = symbol_target(obj, index, definitions)
        if region != "code":
            raise ValueError("Callback is not executable code")
        callbacks.add(offset)
    return callbacks


def freeze(objects: list[CoffObject]) -> tuple[dict, dict]:
    blobs, definitions = allocate_sections(objects)
    relocations = {"code": [], "data": []}
    callbacks = set()
    unwind = []
    for obj in objects:
        for section in obj.sections:
            if section["name"] == ".gfids$y":
                callbacks.update(guard_callbacks(obj, section, definitions))
            if "region" not in section:
                continue
            offsets = section_relocations(obj, section, definitions, relocations)
            if section["name"] == ".pdata":
                unwind.extend(unwind_records(section, offsets))
    return blobs, dict(
        symbols={k: v[1] for k, v in definitions.items() if v[0] == "code"},
        relocations=relocations,
        callbacks=sorted(callbacks),
        unwind=sorted(unwind),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("objects", type=Path, help="Directory containing freshly compiled native_*.obj")
    args = parser.parse_args()
    package = Path(__file__).resolve().parents[1] / "aside_patcher" / "patches" / "bookmark_folder_appearance"
    objects = [CoffObject(path) for path in sorted(args.objects.glob("native_*.obj"))]
    if not objects:
        raise ValueError("No compiled objects")
    blobs, metadata = freeze(objects)
    metadata.update(
        format=1,
        code_size=len(blobs["code"]),
        data_size=len(blobs["data"]),
        sources={path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(package.glob("native_*.[ch]"))},
    )
    data = blobs["code"] + blobs["data"]
    metadata["sha256"] = hashlib.sha256(data).hexdigest()
    (package / "payload.bin").write_bytes(data)
    manifest = (json.dumps(metadata, indent=2) + "\n").encode("utf-8")
    (package / "payload.json").write_bytes(manifest)
    print("manifest sha256:", hashlib.sha256(manifest).hexdigest(), "payload bytes:", len(data))


if __name__ == "__main__":
    main()
