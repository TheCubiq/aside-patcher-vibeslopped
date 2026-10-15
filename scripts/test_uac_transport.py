"""Check native UAC approval or cancellation without changing browser files."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aside_patcher.common.exceptions import Refusal
from aside_patcher.windows import elevate, is_admin


def probe(expected: str) -> dict:
    """Launch only a short interpreter that exits, without installation actions."""
    if is_admin():
        raise ValueError("Start this test without administrator privileges")
    outcome = "approved"
    try:
        code = elevate(["-c", "raise SystemExit(17)"], Path(__file__).resolve().parents[1])
        if code != 17:
            raise ValueError(f"Unexpected probe exit code {code}")
    except Refusal as exc:
        if exc.code != "elevation_declined":
            raise
        outcome = "canceled"
    return {
        "schema_version": 1,
        "transport": "native-windows-uac",
        "expected": expected,
        "observed": outcome,
        "passed": outcome == expected,
        "browser_files_changed": False,
        "runtime_passed": False,
    }


def main() -> int:
    """Ask the operator to approve or decline one harmless native UAC probe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect", choices=("approved", "canceled"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    choice = "No" if args.expect == "canceled" else "Yes"
    print(f"Choose {choice} on the next UAC prompt. This probe changes no browser files.", flush=True)
    report = probe(args.expect)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
