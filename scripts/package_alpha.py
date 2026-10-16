"""Build a standalone Windows GUI with its exact Python engine embedded."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys


def verify_client_modules(build: Path) -> None:
    """Reject developer tests and validation scripts in frozen client modules."""
    _, modules = ast.literal_eval((build / "PYZ-00.toc").read_text(encoding="utf-8"))
    forbidden = []
    for name, source, _ in modules:
        parts = name.split(".")
        source_parts = Path(source).parts
        if (
            any(part in {"tests", "test", "unittest", "pytest", "_pytest"} for part in parts)
            or any(part.startswith("test_") for part in parts)
            or "scripts" in source_parts
        ):
            forbidden.append(name)
    if forbidden:
        raise ValueError(f"Developer modules must not ship: {', '.join(sorted(forbidden))}")


def normalize_build_paths(executable: Path, roots: list[Path]) -> int:
    """Redact same-width compiler home paths only in PE read-only data."""
    image = bytearray(executable.read_bytes())
    pe = struct.unpack_from("<I", image, 60)[0]
    if image[:2] != b"MZ" or image[pe : pe + 4] != b"PE\0\0":
        raise ValueError("Packaging output is not a Windows PE executable")
    count = struct.unpack_from("<H", image, pe + 6)[0]
    table = pe + 24 + struct.unpack_from("<H", image, pe + 20)[0]
    replacements = 0
    for index in range(count):
        section = table + index * 40
        if image[section : section + 8].rstrip(b"\0") != b".rdata":
            continue
        size, offset = struct.unpack_from("<II", image, section + 16)
        data = bytes(image[offset : offset + size])
        for root in roots:
            for value in (str(root).replace("/", "\\") + "\\", str(root).replace("\\", "/") + "/"):
                before = value.encode("utf-8")
                after = b"<build>".ljust(len(before), b"_")
                if len(after) != len(before):
                    raise ValueError("Build path is too short to normalize safely")
                replacements += data.count(before)
                data = data.replace(before, after)
        image[offset : offset + size] = data
    executable.write_bytes(image)
    return replacements


def main():
    """Freeze trusted source assets, then embed that engine in the GPUI executable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.1.0-alpha.2")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    work = root / ".patcher-work" / "alpha" / args.version
    work.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--console",
        "--name",
        "aside-patcher-engine",
        "--distpath",
        str(work / "engine"),
        "--workpath",
        str(work / "build"),
        "--specpath",
        str(work),
        "--paths",
        str(root),
        "--collect-data",
        "aside_patcher",
        "--exclude-module",
        "tests",
        "--exclude-module",
        "unittest",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "_pytest",
        "--add-data",
        f"{root / 'compatibility'}:compatibility",
        str(root / "scripts/frozen_engine.py"),
    ]
    subprocess.run(command, cwd=root, check=True)
    verify_client_modules(work / "build/aside-patcher-engine")
    engine = work / "engine/aside-patcher-engine.exe"
    env = dict(os.environ, ASIDE_PATCHER_ENGINE=str(engine))
    subprocess.run(
        ["cargo", "build", "--manifest-path", "desktop/Cargo.toml", "--features", "packaged-engine"],
        cwd=root,
        env=env,
        check=True,
    )
    destination = work / f"Aside-Patcher-{args.version}-windows-x64.exe"
    shutil.copy2(root / "desktop/target/debug/aside-patcher-desktop.exe", destination)
    private_roots = [Path.home()]
    if len(root.parts) >= 3 and root.parts[1].lower() == "users":
        private_roots.append(Path(*root.parts[:3]))
    normalized = normalize_build_paths(destination, private_roots)
    if engine.read_bytes() not in destination.read_bytes():
        raise ValueError("Embedded engine changed during packaging")
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    (work / "SHA256SUMS.txt").write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    metadata = {
        "version": args.version,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()),
        "executable": destination.name,
        "sha256": digest,
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "standalone": True,
        "developer_tests_included": False,
        "externally_loaded_packages": False,
        "normalized_build_paths": normalized,
    }
    (work / "build-info.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Standalone alpha: {destination}")


if __name__ == "__main__":
    main()
