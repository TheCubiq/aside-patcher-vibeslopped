from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import (
    Refusal,
    apply_to_bytes,
    atomic_replace,
    backup_directory,
    ensure_immutable_backup,
    file_sha256,
    load_build,
    read_binary,
    require_app_stopped,
    restore_bytes,
    state_for,
)

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "builds.json"
TARGETS = ("chats", "ask-aside", "chats-toggle")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aside-patcher")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser(
        "status", help="Show the installed DLL version and patch state."
    )
    status.add_argument("--binary", type=Path, help=argparse.SUPPRESS)

    apply = commands.add_parser("apply", help="Apply one or more verified UI patches.")
    apply.add_argument("--target", choices=TARGETS, action="append", required=True)
    apply.add_argument("--binary", type=Path, help=argparse.SUPPRESS)

    restore = commands.add_parser(
        "restore", help="Restore the immutable original DLL backup."
    )
    restore.add_argument("--binary", type=Path, help=argparse.SUPPRESS)
    return parser


def _target_path(build, override: Path | None) -> Path:
    return override or build.binary_path


def _state_label(targets: tuple[str, ...] | None) -> str:
    if targets is None:
        return "unknown / unsupported"
    if not targets:
        return "original"
    return "patched: " + ", ".join(targets)


def run_status(build, path: Path) -> int:
    digest = file_sha256(path)
    state = build.states.get(digest)
    print(f"Aside build: {build.version}")
    print(f"Binary: {path}")
    print(f"SHA-256: {digest}")
    print(f"State: {_state_label(state)}")
    for target in TARGETS:
        if target in build.patches:
            print(f"{target}: verified patch available")
        else:
            print(f"{target}: no verified patch configured; apply is disabled")
    return 0 if state is not None else 2


def run_apply(build, path: Path, targets: list[str]) -> int:
    require_app_stopped()
    original = read_binary(path)
    result = apply_to_bytes(original, build, targets)
    if result == original:
        print("Requested target(s) are already applied.")
        return 0
    backup_root = backup_directory()
    if state_for(original, build):
        backup_path = backup_root / f"{build.original_sha256}.dll"
        ensure_immutable_backup(read_binary(backup_path), build, backup_root)
    else:
        ensure_immutable_backup(original, build, backup_root)
    require_app_stopped()
    atomic_replace(path, result)
    print(f"Applied {', '.join(dict.fromkeys(targets))} to {path}")
    print(f"SHA-256: {file_sha256(path)}")
    return 0


def run_restore(build, path: Path) -> int:
    require_app_stopped()
    current = read_binary(path)
    state = state_for(current, build)
    if state is None:
        raise Refusal(
            f"Refusing to restore unknown installed hash: {file_sha256(path)}"
        )
    if not state:
        print("Aside.dll already matches the supported original build.")
        return 0

    backup = backup_directory() / f"{build.original_sha256}.dll"
    original = read_binary(backup)
    restored = restore_bytes(current, original, build)
    require_app_stopped()
    atomic_replace(path, restored)
    print(f"Restored the original {build.version} binary from {backup}")
    print(f"SHA-256: {file_sha256(path)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        build = load_build(MANIFEST)
        from .installation import Installation, run_installation

        if isinstance(build, Installation):
            if args.binary:
                raise Refusal(
                    "--binary is only supported by legacy single-file manifests."
                )
            return run_installation(build, args.command, getattr(args, "target", None))
        path = _target_path(build, args.binary)
        if args.command == "status":
            return run_status(build, path)
        if args.command == "apply":
            return run_apply(build, path, args.target)
        return run_restore(build, path)
    except Refusal as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
