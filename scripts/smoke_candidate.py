"""Smoke-test a private candidate in a confined Windows fixture, then restore it."""

# Direct execution needs the repository root before importing the engine.
# pylint: disable=wrong-import-position

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aside_patcher.common.models import MANIFEST_PATH
from aside_patcher.common.utils import file_sha256
from aside_patcher.compatibility import build_from_package
from aside_patcher.discovery import resolve_installation
from aside_patcher.protocol import inspect_build
from aside_patcher.recipes import prepare_changes
from aside_patcher.transaction import replace_transaction
from aside_patcher.windows import browser_processes, close_browser, installation_lock, require_browser_stopped
from scripts.native_ui import NativeUI
from scripts.content_ui import WebUI
from scripts.run_native_acceptance import launch_fixture
from scripts.test_browser_restart import seed_profile
from scripts.test_desktop_engine import trial_context


def install(build, executable: Path, targets: list) -> dict:
    """Exercise engine recipes, locks, transactions and exact hashes without redundant rendering."""
    with installation_lock(executable.parent / build.version):
        require_browser_stopped(executable)
        desired = tuple(sorted(targets))
        state = inspect_build(build, native_only=True)
        if desired not in build.states:
            raise ValueError("Selection has no verified candidate output")
        if tuple(state["current"]) == desired:
            return state
        changes = prepare_changes(build, state["hashes"], desired)
        journal = executable.parent / build.version / ".aside-patcher-transaction.json"
        replace_transaction(changes, journal, guard=lambda: require_browser_stopped(executable))
        result = inspect_build(build, native_only=True)
        if result["hashes"] != build.states[desired] or sorted(result["current"]) != sorted(targets):
            raise ValueError("Installed candidate differs from its verified preview")
        return result


def capture(executable: Path, destination: Path) -> dict:
    """Capture only the fixture window; no foreground stealing or screen coordinates."""
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path(__file__).with_name("capture_browser.ps1")),
            "-Executable",
            str(executable),
            "-Output",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Fixture capture failed: " + result.stderr[:500])
    return {"path": destination.name, "sha256": file_sha256(destination)}


def settle(ui: NativeUI) -> None:
    """Use a short bounded layout check followed by a fixed fallback delay."""
    previous = None
    stable = 0
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        node = ui.find("AsideSectionHeaderButton", "Bookmarks")
        properties = ui.properties(node)
        bounds = tuple(properties.get(key) for key in ("X", "Y", "Width", "Height"))
        stable = stable + 1 if bounds == previous else 0
        if stable >= 3:
            return
        previous = bounds
        time.sleep(0.15)
    time.sleep(1.5)


def check_effects(ui: NativeUI, targets: list, restored: bool = False) -> dict:
    """Check only each patch's primary visible effect; no exhaustive interaction tour."""
    observed = {}
    for patch_id, selector in (
        ("ask-aside", {"class_name": "LabelButtonLabel", "text": "Ask Aside"}),
        ("chats", {"class_name": "AsideSectionHeaderButton", "text": "Chats"}),
    ):
        if patch_id not in targets:
            continue
        expected = 0 if patch_id in targets and not restored else 1
        ui.wait(lambda selector=selector, expected=expected: len(ui.matches(**selector)) == expected)
        observed[patch_id] = {"visible_count": expected, "passed": True}
    if "bookmark-folder-appearance" in targets and not restored:
        label = ui.find("Label", "Acceptance Parent")
        before = ui.properties(label)["--EnabledColor"]
        ui.click("VerticalBookmarkRowView", "Acceptance Parent", "right")
        ui.wait(lambda: len(ui.matches("TabGroupColorButton")) == 8)
        ui.click_selector({"class_name": "TabGroupColorButton", "index": 5, "expected_count": 8})
        ui.wait(lambda: ui.properties(ui.find("Label", "Acceptance Parent"))["--EnabledColor"] != before)
        observed["bookmark-folder-appearance"] = {"palette_count": 8, "color_changed": True, "passed": True}
    return observed


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def launch_check(args, executable: Path, profile: Path, targets: list, name: str, restored: bool = False) -> dict:
    """Launch, check primary effects, retain a settled screenshot, and close gracefully."""
    native = browser = page = None
    try:
        native, browser = launch_fixture(executable, profile, args)
        page = WebUI(browser, args.cdp_port, "chrome://bookmarks/", "location.host === 'bookmarks' && !!chrome.bookmarks")
        browser.call("Target.activateTarget", targetId=page.target)
        ui = NativeUI(native)
        ui.wait(lambda: ui.find("AsideSectionHeaderButton", "Bookmarks"))
        settle(ui)
        effects = check_effects(ui, targets, restored)
        time.sleep(1.5)
        screenshot = capture(executable, args.report.parent / f"{name}.png")
        return {"effects": effects, "screenshot": screenshot}
    except (OSError, RuntimeError, ValueError, TimeoutError) as error:
        if native:
            (args.report.parent / f"{name}-failure.tree.json").write_text(json.dumps(NativeUI(native).tree()), encoding="utf-8")
        try:
            capture(executable, args.report.parent / f"{name}-failure.png")
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            pass
        raise RuntimeError(f"{name}: {error}") from error
    finally:
        if page:
            page.close()
        if browser:
            try:
                browser.call("Browser.close")  # Graceful browser quit, including its background windows.
            except (OSError, RuntimeError, TimeoutError):
                pass  # A successful quit can close the socket before replying; process checks below remain mandatory.
        if native:
            native.close()
        if browser:
            browser.close()
        close_browser(executable)


def save_report(args, report: dict) -> None:
    """Persist progress so failures never erase already collected evidence."""
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


# pylint: disable-next=too-many-arguments,too-many-positional-arguments
def smoke_case(args, build, executable: Path, targets: list, index: int, outcome: dict) -> None:
    """Keep failed patches independent and always restore before testing another selection."""
    profile = args.trial / f"smoke-profile-{uuid.uuid4().hex}"
    seed_profile(profile)
    fixture = Path(__file__).resolve().parents[1] / "tests/fixtures/automation-bookmarks.json"
    (profile / "Default/Bookmarks").write_bytes(fixture.read_bytes())
    started = time.monotonic()
    try:
        install(build, executable, targets)
        patched = launch_check(args, executable, profile, targets, f"{index:02d}-patched")
        outcome["effects"] = patched["effects"]
        outcome["screenshots"].append(patched["screenshot"])
    except (OSError, RuntimeError, ValueError, TimeoutError, KeyError) as error:
        outcome["error"] = str(error)
    finally:
        close_browser(executable)
        install(build, executable, [])
    restored = launch_check(args, executable, profile, targets, f"{index:02d}-restored", restored=True)
    outcome["screenshots"].append(restored["screenshot"])
    outcome["elapsed_seconds"] = round(time.monotonic() - started, 2)
    if outcome["elapsed_seconds"] > 300:
        raise TimeoutError("Smoke case exceeded five-minute budget; originals restored")
    outcome["passed"] = "error" not in outcome


def exercise(args) -> dict:  # pylint: disable=too-many-statements,too-many-locals
    """Test all file transactions and only the required individual/interaction launches."""
    if not args.allow_unsigned_candidate:
        raise ValueError("Private developer fixtures require explicit --allow-unsigned-candidate")
    context = trial_context(args.trial)
    package_path = args.candidate / "package.unsigned.json"
    candidate = json.loads((args.candidate / "candidate.json").read_text())
    if file_sha256(package_path) != candidate["candidate_sha256"]:
        raise ValueError("Candidate changed after generation")
    version = candidate["browser_version"]
    executable = Path(context["installation"]).parent.parent / "Aside.exe"
    if browser_processes(executable):
        raise ValueError("Fixture browser must be closed before testing")
    adapter = build_from_package(json.loads(package_path.read_bytes()), version)
    item = {"executable": str(executable), "dll": context["installation"], "version": version}
    build = resolve_installation(MANIFEST_PATH, item, Path(context["data_root"]), native_only=True, adapter=adapter)
    initial = inspect_build(build, native_only=True)
    if initial["current"]:
        raise ValueError("Smoke fixture must start from exact vendor originals")
    reusable = {}
    report = {
        "schema_version": 2,
        "candidate_sha256": candidate["candidate_sha256"],
        "browser_version": version,
        "runtime_passed": False,
        "checks": {},
        "transactions": [],
        "combinations": [],
        "status": "running",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.resume and args.report.exists():
        previous = json.loads(args.report.read_text())
        expected = {tuple(targets) for targets in build.states}
        verified = {tuple(item["targets"]) for item in previous.get("transactions", []) if item.get("passed") is True}
        if previous.get("candidate_sha256") == candidate["candidate_sha256"] and verified == expected:
            report["transactions"] = previous["transactions"]
            report["reused_transactions"] = True
            (args.report.parent / "previous-transactions.json").write_text(json.dumps(previous, indent=2), encoding="utf-8")
            for outcome in previous.get("combinations", []):
                evidence = outcome.get("screenshots", [])
                if (
                    outcome.get("passed") is True
                    and len(evidence) >= 2
                    and all(
                        (args.report.parent / item["path"]).resolve().is_relative_to(args.report.parent.resolve())
                        and (args.report.parent / item["path"]).is_file()
                        and file_sha256(args.report.parent / item["path"]) == item["sha256"]
                        for item in evidence
                    )
                ):
                    reusable[tuple(outcome["targets"])] = outcome
    save_report(args, report)
    try:
        if not report["transactions"]:
            for targets in build.states:
                install(build, executable, list(targets))
                report["transactions"].append({"targets": list(targets), "passed": True})
                save_report(args, report)
        install(build, executable, [])
        report["checks"]["all-combinations"] = True
        for index, targets in enumerate(candidate["runtime_selections"]):
            if tuple(targets) in reusable:
                report["combinations"].append(dict(reusable[tuple(targets)], reused=True))
                save_report(args, report)
                continue
            outcome = {"targets": targets, "passed": False, "screenshots": []}
            report["combinations"].append(outcome)
            save_report(args, report)
            smoke_case(args, build, executable, targets, index, outcome)
            save_report(args, report)
        all_passed = all(item["passed"] for item in report["combinations"])
        report["checks"].update(launch=all_passed, **{"patch-behavior": all_passed}, restore=True)
        report["runtime_passed"] = all_passed
    except (OSError, RuntimeError, ValueError, TimeoutError, KeyError) as error:
        report["error"] = str(error)
    finally:
        try:
            close_browser(executable)
            install(build, executable, [])
            report["restored"] = inspect_build(build, native_only=True)["hashes"] == initial["hashes"]
        except (OSError, RuntimeError, ValueError) as error:
            report.update(restored=False, restoration_error=str(error))
        report["runtime_passed"] = report["runtime_passed"] and report["restored"]
        report["status"] = "finished"
        save_report(args, report)
    return report


def main() -> None:
    """Expose a private-candidate fixture runner, never an end-user installation bypass."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-unsigned-candidate", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Reuse completed transactions for an unchanged, restored candidate")
    parser.add_argument("--cdp-port", type=int, default=9457)
    parser.add_argument("--native-port", type=int, default=9458)
    args = parser.parse_args()
    report = exercise(args)
    print(json.dumps(report))
    if not report["runtime_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
