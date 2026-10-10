"""Expose verified patch operations through the command line."""

from __future__ import annotations

import argparse
import sys

from .common.exceptions import Refusal
from .common.models import MANIFEST_PATH, TARGETS
from .installation import run_installation
from .manifest import load_build


def _parser() -> argparse.ArgumentParser:
    """Create the command-line parser for status, apply, and restore.

    Returns
    -------
    ArgumentParser
        Parser for the patcher command line.
    """
    parser = argparse.ArgumentParser(prog="aside-patcher")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show installed file hashes and patch state.")

    apply = commands.add_parser("apply", help="Apply one or more verified UI patches.")
    apply.add_argument("--target", choices=TARGETS, action="append", required=True)

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
        build = load_build(MANIFEST_PATH)
        return run_installation(build, args.command, getattr(args, "target", None))
    except Refusal as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
