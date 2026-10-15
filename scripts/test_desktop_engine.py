"""Exercise complete selections on a separately prepared original-browser fixture."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aside_patcher.protocol import dispatch


def trial_context(root: Path) -> dict:
    """Require both the browser and settings profile inside the isolated trial."""
    root = root.resolve()
    context = json.loads((root / "request-context.json").read_text(encoding="utf-8-sig"))
    for field in ("installation", "data_root"):
        if not Path(context[field]).resolve().is_relative_to(root):
            raise ValueError(f"Fixture {field} escaped the supplied trial directory")
    return context


def exercise(context: dict) -> dict:
    """Validate file transactions and restore originals even after a failed case."""
    state = dispatch(dict(context, operation="inspect"))
    if not state.get("combinations"):
        raise ValueError("Need a reviewed browser adapter with advertised combinations")
    if state["current"]:
        raise ValueError("Start combination tests from an original isolated fixture")
    results = []
    try:
        for targets in state["combinations"]:
            targets = list(targets)
            preview = dispatch(dict(context, operation="preview", targets=targets))
            installed = dispatch(dict(context, operation="apply", targets=targets, preview_token=preview["token"]))
            if sorted(installed["current"]) != sorted(targets) or installed["hashes"] != preview["expected"]:
                raise ValueError("Installed state differs from verified preview")
            results.append({"targets": targets, "file_transaction_passed": True})
            print("transaction passed:", ",".join(targets) or "originals", flush=True)
    finally:
        journal = Path(context["installation"]).with_name(".aside-patcher-transaction.json")
        if journal.exists():
            dispatch(dict(context, operation="recover"))
        preview = dispatch(dict(context, operation="preview", targets=[]))
        operation = "restore" if state.get("restore_supported") else "apply"
        restored = dispatch(dict(context, operation=operation, targets=[], preview_token=preview["token"]))
        if restored["current"] or restored["hashes"] != preview["expected"]:
            raise ValueError("Isolated trial did not restore exact original hashes")
    return {
        "schema_version": 1,
        "file_transactions_passed": True,
        "restored": True,
        "scope": "native-only" if state.get("native_only") else "browser-and-settings",
        "runtime_passed": False,
        "reason": "File-transaction checks do not establish browser UI behavior.",
        "combinations": results,
    }


def main() -> None:
    """Validate every file transaction without claiming interactive UI coverage."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = exercise(trial_context(args.trial))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
