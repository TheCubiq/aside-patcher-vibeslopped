"""Expose verified patch operations through the command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import Refusal
from .legacy import Build, run_apply, run_restore, run_status

from .installation import Installation, run_installation
from .manifest import load_build

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "builds.json"
TARGETS = ("chats", "ask-aside", "chats-toggle")


def _parser() -> argparse.ArgumentParser:
    """Create the command-line parser for status, apply, and restore.

    Returns
    -------
    ArgumentParser
        Parser for the patcher command line.
    """
    parser = argparse.ArgumentParser(prog="aside-patcher")
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="Show the installed DLL version and patch state.")
    status.add_argument("--binary", type=Path, help=argparse.SUPPRESS)

    apply = commands.add_parser("apply", help="Apply one or more verified UI patches.")
    apply.add_argument("--target", choices=TARGETS, action="append", required=True)
    apply.add_argument("--binary", type=Path, help=argparse.SUPPRESS)

    restore = commands.add_parser("restore", help="Restore the immutable original DLL backup.")
    restore.add_argument("--binary", type=Path, help=argparse.SUPPRESS)
    return parser


def _target_path(build: Build, override: Path | None) -> Path:
    """Choose an explicit legacy binary path or the manifest default.

    Parameters
    ----------
    build : Build
        Supported build definition and hash allowlist.
    override : Path or None
        Optional path supplied by the legacy binary override.

    Returns
    -------
    Path
        Selected legacy binary path.
    """
    return override or build.binary_path


def main(argv: list[str] | None = None) -> int:
    """Dispatch the requested patcher command and report refusals.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; None uses the process arguments.

    Returns
    -------
    int
        Exit code: zero on success, or two for an unsupported state or refusal.
    """
    args = _parser().parse_args(argv)
    try:
        build = load_build(MANIFEST)
        if isinstance(build, Installation):
            if args.binary:
                raise Refusal("--binary is only supported by legacy single-file manifests.")
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
