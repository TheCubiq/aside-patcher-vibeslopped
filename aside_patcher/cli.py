"""Expose verified patch operations through the command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .common.exceptions import Refusal
from .common.models import MANIFEST_PATH
from .installation import run_installation
from .native_scope import native_only_build
from .manifest import load_build
from .patches import PATCHES


def _parser() -> argparse.ArgumentParser:
    """Create the command-line parser for patch listing and installation commands.

    Returns
    -------
    ArgumentParser
        Parser for the patcher command line.
    """
    parser = argparse.ArgumentParser(prog="aside-patcher")
    parser.add_argument(
        "--manifest", type=Path, default=MANIFEST_PATH, help="Supported build manifest; useful for verified copy trials."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    parser.add_argument(
        "--native-only", action="store_true", help="Inspect the DLL or change bookmark appearance without settings files."
    )
    commands.add_parser("list", help="List available patches and their affected files.")
    commands.add_parser("status", help="Show installed file hashes and patch state.")
    choices = tuple(patch.patch_id for patch in PATCHES)
    for command in ("enable", "disable"):
        toggle = commands.add_parser(command, help=f"{command.capitalize()} selected patches while preserving the rest.")
        toggle.add_argument("target", nargs="+", choices=choices, metavar="PATCH")
    commands.add_parser("restore", help="Restore all affected files from verified original backups.")
    return parser


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
        build = load_build(args.manifest)
        if args.native_only:
            if args.command != "status" and (
                args.command not in {"enable", "disable"} or set(args.target) != {"bookmark-folder-appearance"}
            ):
                raise Refusal("Native-only mode supports status and enabling/disabling bookmark-folder-appearance only.")
            build = native_only_build(build)
        if args.command == "list":
            for patch in build.patches.values():
                print(f"{patch.patch_id}: {patch.description}")
                print(f"  Files: {', '.join(patch.files)}; conflicts: {', '.join(patch.conflicts) or 'none'}")
            return 0
        return run_installation(build, args.command, getattr(args, "target", None))
    except Refusal as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
