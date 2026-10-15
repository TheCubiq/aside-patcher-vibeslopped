"""Exercise graceful shutdown and unelevated restart on a fresh isolated browser."""

import argparse
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aside_patcher import protocol
from aside_patcher.windows import browser_processes, close_browser, is_admin, launch_arguments
from scripts.test_desktop_engine import trial_context


def seed_profile(profile: Path) -> None:
    """Initialize only a new synthetic profile, preserving archived settings assets."""
    default = profile / "Default"
    if default.exists() or (profile / "Local State").exists():
        raise ValueError("Lifecycle test requires a fresh profile, never an existing browser profile")
    default.mkdir(parents=True)
    preferences = {
        "aside": {"bootstrap_complete": True, "onboarding_completed": True},
        "background_mode": {"enabled": False},
        "browser": {"check_default_browser": False},
    }
    (default / "Preferences").write_text(json.dumps(preferences), encoding="utf-8")
    (profile / "Local State").write_text(json.dumps({"aside": {"onboarding_completed": True}}), encoding="utf-8")


def exercise(context: dict, report: dict) -> None:
    """Apply and remove one native patch while preserving the captured profile."""
    if is_admin():
        raise ValueError("Run lifecycle acceptance without administrator privileges")
    dll = Path(context["installation"])
    executable = dll.parent.parent / "Aside.exe"
    if browser_processes(executable):
        raise ValueError("Trial browser must be stopped before starting lifecycle acceptance")
    profile = Path(context["data_root"]).parent / f"lifecycle-profile-{uuid.uuid4().hex}"
    context = dict(context, data_root=str(profile))
    state = protocol.dispatch(dict(context, operation="inspect"))
    if state["current"]:
        raise ValueError("Lifecycle test must start with vendor originals")
    original = state["hashes"]["dll"]
    seed_profile(profile)
    report["synthetic_profile"] = str(profile)
    arguments = [
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--disable-component-update",
        "about:blank",
    ]
    try:
        protocol._restart_browser(str(executable), arguments)  # pylint: disable=protected-access
        report["initial_launch_passed"] = True
        captured = launch_arguments(executable)
        detected = protocol.dispatch({"protocol_version": 1, "operation": "inspect", "installation": str(dll)})
        if Path(detected["data_root"]) != profile:
            raise ValueError("Automatic discovery did not select the running browser's synthetic profile")
        report["profile_discovery_passed"] = True
        for targets in (["ask-aside"], []):
            preview = protocol.dispatch(dict(context, operation="preview", targets=targets))
            result = protocol.dispatch(dict(context, operation="apply", targets=targets, preview_token=preview["token"]))
            if not result["restarted"] or sorted(result["current"]) != targets:
                raise ValueError("Expected installed selection and a restarted browser")
            if launch_arguments(executable) != captured:
                raise ValueError("Restart changed the original synthetic profile arguments")
            report["apply_restart_passed" if targets else "restore_restart_passed"] = True
        if result["hashes"]["dll"] != original:
            raise ValueError("Restoration did not recover the exact original browser image")
        report["original_hash_restored"] = True
    finally:
        close_browser(executable)
        report["final_shutdown_passed"] = True
        preview = protocol.dispatch(dict(context, operation="preview", targets=[]))
        restored = protocol.dispatch(dict(context, operation="apply", targets=[], preview_token=preview["token"]))
        if restored["hashes"]["dll"] != original:
            raise ValueError("Lifecycle cleanup could not restore the exact original image")
        report["cleanup_restored"] = True


def main() -> int:
    """Retain diagnostics even when shutdown, launch, or restoration is refused."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    context = trial_context(args.trial)
    report = {"schema_version": 1, "lifecycle_passed": False, "runtime_passed": False}
    try:
        exercise(context, report)
        report["lifecycle_passed"] = True
    except (OSError, ValueError, protocol.Refusal) as exc:
        report["error"] = str(exc)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["lifecycle_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
