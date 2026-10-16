"""Build a standalone Windows GUI with its exact Python engine embedded."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    """Freeze trusted source assets, then embed that engine in the GPUI executable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.1.0-alpha.1")
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
        "--collect-submodules",
        "aside_patcher",
        "--add-data",
        f"{root / 'compatibility'}:compatibility",
        str(root / "scripts/frozen_engine.py"),
    ]
    subprocess.run(command, cwd=root, check=True)
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
        "externally_loaded_packages": False,
    }
    (work / "build-info.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Standalone alpha: {destination}")


if __name__ == "__main__":
    main()
