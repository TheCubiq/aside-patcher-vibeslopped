"""Test native UAC apply/restore against a dedicated protected browser fixture."""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aside_patcher import protocol


def trial_context(root: Path) -> dict:
    """Restrict this developer test to its dedicated Program Files directory."""
    allowed_root = Path(os.environ["ProgramFiles"]) / "Aside Patcher Development"
    root = root.resolve()
    if root == allowed_root.resolve() or not root.is_relative_to(allowed_root.resolve()):
        raise ValueError("Protected tests require a dedicated Aside Patcher Development subdirectory")
    context = json.loads((root / "request-context.json").read_text(encoding="utf-8-sig"))
    for field in ("installation", "data_root"):
        if not Path(context[field]).resolve().is_relative_to(root):
            raise ValueError(f"Test {field} escaped its dedicated fixture")
    return context


def exercise(context: dict) -> dict:
    """Apply both protected artifacts and restore exact originals afterward."""
    targets = ["ask-aside", "bookmark-folder-appearance", "chats-toggle"]
    preview = protocol.dispatch(dict(context, operation="preview", targets=targets))
    if not preview["requires_elevation"] or len(preview["affected"]) != 2:
        raise ValueError("Expected two protected original artifacts and an unelevated caller")

    try:
        installed = protocol.dispatch(dict(context, operation="apply", targets=targets, preview_token=preview["token"]))
        if installed["hashes"] != preview["expected"]:
            raise ValueError("Elevated helper did not install the reviewed hashes")
    finally:
        journal = Path(context["installation"]).with_name(".aside-patcher-transaction.json")
        if journal.exists():
            protocol.dispatch(dict(context, operation="recover"))
        restore_preview = protocol.dispatch(dict(context, operation="preview", targets=[]))
        restored = protocol.dispatch(dict(context, operation="restore", targets=[], preview_token=restore_preview["token"]))

    if restored["current"]:
        raise ValueError("Protected trial did not return to vendor originals")
    return {
        "helper_roundtrip_passed": True,
        "restored": True,
        "elevation_transport": "native-windows-uac",
        "native_uac_transport_tested": True,
        "runtime_passed": False,
    }


def main() -> None:
    """Request native Windows approval for each protected test operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    context = trial_context(args.trial)
    report = exercise(context)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Native UAC helper apply/restore passed; native patch UI acceptance remains pending.")


if __name__ == "__main__":
    main()
