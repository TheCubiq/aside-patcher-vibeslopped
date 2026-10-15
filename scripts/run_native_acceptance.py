"""Run deterministic native Views scenarios against a separately prepared trial."""

# Direct execution needs the repository root before importing the engine.
# pylint: disable=wrong-import-position

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import ProxyHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aside_patcher.protocol import dispatch  # noqa: E402
from aside_patcher.windows import browser_processes, close_browser  # noqa: E402
from scripts.native_ui import Connection, NativeUI, ProtocolError  # noqa: E402
from scripts.content_ui import WebUI, bookmark_call, read_bookmark, set_theme  # noqa: E402
from scripts.windows_input import RoutedInput  # noqa: E402
from scripts.native_matrix import suite_for_targets  # noqa: E402
from scripts.test_browser_restart import seed_profile  # noqa: E402
from scripts.test_desktop_engine import trial_context  # noqa: E402


def http_json(port: int, path: str) -> dict:
    """Read only loopback endpoints, bypassing proxy environment settings."""
    opener = build_opener(ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/{path}", timeout=2) as response:
        return json.load(response)


def connect_native(port: int, timeout: float = 15) -> Connection:
    """Await Chromium's native socket directly; this server has no /json route."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return Connection(f"ws://127.0.0.1:{port}/0", open_timeout=2)
        except (OSError, TimeoutError, ProtocolError):
            time.sleep(0.1)
    raise TimeoutError("Native UI DevTools did not become available")


def pointer_step(ui: NativeUI, selector: dict, observations: dict | None, button: str | None) -> None:
    """Choose real Windows routing for integration runs and callbacks for focused tests."""
    if observations and observations.get("routed_input"):
        RoutedInput(ui, observations["executable"]).perform(selector, button)
    elif button is None:
        ui.hover(**selector)
    else:
        ui.click_selector(selector, button)


def run_step(ui: NativeUI, step: dict, observations: dict | None = None) -> None:
    """Dispatch a bounded action or verify an observable native postcondition."""
    action = step["action"]
    selector = step.get("selector", {})
    if action == "click":
        pointer_step(ui, selector, observations, step.get("button", "left"))
    elif action == "hover":
        pointer_step(ui, selector, observations, None)
    elif action == "count":
        ui.wait(lambda: len(ui.matches(**selector)) == step["expected"])
    elif action == "property":
        ui.wait(lambda: ui.properties(ui.find(**selector)).get(step["name"]) == step["expected"])
    elif action == "remember":
        observations[step["as"]] = ui.wait(lambda: ui.properties(ui.find(**selector))[step["name"]])
    elif action in ("changed", "unchanged"):
        baseline = observations[step["from"]]
        ui.wait(lambda: (ui.properties(ui.find(**selector))[step["name"]] != baseline) == (action == "changed"))
    elif action == "click-index":
        indexed = dict(selector, index=step["index"], expected_count=step["expected_count"])
        pointer_step(ui, indexed, observations, step.get("button", "left"))
    elif action == "open-tab":
        target = observations["browser"].call("Target.createTarget", url=step["url"])
        observations["browser"].call("Target.activateTarget", targetId=target["targetId"])
    elif action == "bookmark":
        bookmark_call(observations["browser"], observations["cdp_port"], step["method"], step["arguments"])
    elif action == "theme":
        set_theme(observations["browser"], observations["cdp_port"], step["value"])
    else:
        run_routed_step(ui, step, observations)


def run_routed_step(ui: NativeUI, step: dict, observations: dict | None) -> None:
    """Route Windows input and assert the resulting native menu or bookmark model."""
    action = step["action"]
    selector = step.get("selector", {})
    if action in ("real-click", "real-hover"):
        if not observations.get("routed_input"):
            raise ValueError("This case needs --routed-input on a dedicated interactive test desktop")
        driver = RoutedInput(ui, observations["executable"])
        driver.perform(selector, step.get("button", "left") if action == "real-click" else None)
    elif action == "uia-menu":
        driver = RoutedInput(ui, observations["executable"])
        ui.wait(lambda: set(driver.menu_items()) == set(step["expected"]))
    elif action == "real-drag":
        if not observations.get("routed_input"):
            raise ValueError("Real drag tests require --routed-input on a dedicated interactive desktop")
        if "bookmark_reader" not in observations:
            observations["bookmark_reader"] = observations["resources"].enter_context(
                WebUI(
                    observations["browser"],
                    observations["cdp_port"],
                    "chrome://bookmarks/",
                    "location.protocol === 'chrome:' && !!globalThis.chrome?.bookmarks",
                )
            )
        RoutedInput(ui, observations["executable"]).drag(
            step["source"], step["target"], tuple(step.get("fraction", (0.5, 0.5))), step.get("edge")
        )
    elif action == "bookmark-assert":
        page = observations["bookmark_reader"]
        ui.wait(lambda: all(read_bookmark(page, step["reference"]).get(key) == value for key, value in step["expected"].items()))
    else:
        raise ValueError(f"Unknown native scenario action: {action}")


def scenarios(ui: NativeUI, suite: dict, destination: Path, report: dict, observations: dict) -> None:
    """Retain individual failures and the full native tree/protocol transcript."""
    for case in suite["cases"]:
        outcome = {"id": case["id"], "passed": False}
        report["cases"].append(outcome)
        try:
            for step in case["steps"]:
                run_step(ui, step, observations)
            outcome["passed"] = True
        except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as error:
            outcome["error"] = str(error)
            raise
        finally:
            try:
                (destination / f"{case['id']}.tree.json").write_text(json.dumps(ui.tree(), indent=2), encoding="utf-8")
            finally:
                (destination / "native-transcript.json").write_text(json.dumps(ui.connection.transcript, indent=2), encoding="utf-8")


def validate_suite(suite: dict, version: str) -> None:
    """Validate adapter identity and evidence names before touching any files."""
    if suite.get("schema_version") != 1 or suite["browser_version"] != version:
        raise ValueError("Native scenario adapter does not match the browser version or schema")
    if not suite["cases"] or len({case["id"] for case in suite["cases"]}) != len(suite["cases"]):
        raise ValueError("Native suite requires nonempty cases with unique IDs")
    for case in suite["cases"]:
        if not re.fullmatch(r"[a-z0-9-]+", case["id"]):
            raise ValueError("Scenario ID must be a safe evidence filename")
        if not case["steps"]:
            raise ValueError("Native cases require observable steps")


def verify_profile(profile: Path, suite: dict) -> dict:
    """Check persisted folder identity records after a complete browser shutdown."""
    preferences = json.loads((profile / "Default" / "Preferences").read_text(encoding="utf-8-sig"))
    lines = preferences.get("aside", {}).get("bookmark_folder_appearance", "").splitlines()
    if lines and lines[0] != "v1":
        raise ValueError("Unexpected stored appearance format")
    observed = {}
    for line in lines[1:]:
        guid, color, icon = line.split("\t")
        if guid in observed:
            raise ValueError("Duplicate folder identity in appearance preference")
        observed[guid] = {"color": int(color) if color else None, "icon": icon or None}
    for guid, expected in suite.get("expected_appearance", {}).items():
        if observed.get(guid) != expected:
            raise ValueError(f"Persisted appearance mismatch for {guid}: {observed.get(guid)}")
    for guid in suite.get("absent_appearance", []):
        if guid in observed:
            raise ValueError(f"Deleted folder decoration survived: {guid}")
    return observed


def record_profile_storage(profile: Path, suite: dict, report: dict) -> None:
    """Record storage failures without interrupting mandatory binary restoration."""
    try:
        report["stored_appearance"] = verify_profile(profile, suite)
        report["profile_storage_passed"] = True
    except (OSError, ValueError) as error:
        report.update(scenario_passed=False, profile_storage_passed=False, error=str(error))


def launch_fixture(executable: Path, profile: Path, args: argparse.Namespace) -> tuple:
    """Launch only the selected image and bind both protocols to its profile."""
    subprocess.Popen(  # pylint: disable=consider-using-with
        [
            str(executable),
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={args.cdp_port}",
            f"--enable-ui-devtools={args.native_port}",
            "--enable-features=ui-debug-tools-enable-synthetic-events",
            "--enable-automation",
            "--lang=en-US",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-mode",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    native = connect_native(args.native_port)
    browser = None
    try:
        browser = Connection(http_json(args.cdp_port, "json/version")["webSocketDebuggerUrl"])
        arguments = browser.call("Browser.getBrowserCommandLine")["arguments"]
        if f"--user-data-dir={profile}" not in arguments:
            raise ValueError("CDP endpoint does not belong to this synthetic profile")
        return native, browser
    except (OSError, RuntimeError, TimeoutError):
        native.close()
        if browser:
            browser.close()
        raise


def restart_checks(args: argparse.Namespace, profile: Path, checks: list, report: dict, phase: str = "restart") -> None:
    """Verify actual rendering after all original browser processes have exited."""
    executable = Path(report["installation"]).parent.parent / "Aside.exe"
    native, browser = launch_fixture(executable, profile, args)
    ui = NativeUI(native)
    try:
        for step in checks:
            outcome = {"step": step, "passed": False}
            report.setdefault(f"{phase}_checks", []).append(outcome)
            run_step(ui, step, {"routed_input": args.routed_input, "executable": executable})
            outcome["passed"] = True
    finally:
        try:
            destination = Path(report["evidence_directory"])
            (destination / f"{phase}.tree.json").write_text(json.dumps(ui.tree(), indent=2), encoding="utf-8")
            (destination / f"{phase}-transcript.json").write_text(json.dumps(native.transcript, indent=2), encoding="utf-8")
        finally:
            native.close()
            browser.close()
            close_browser(executable)


def resolve_checks(checks: list, observations: dict) -> list:
    """Bind restart expectations to values observed before process shutdown."""
    resolved = []
    for check in checks:
        step = dict(check)
        if "from" in step:
            step["expected"] = observations[step.pop("from")]
        resolved.append(step)
    return resolved


# Keep shutdown and mandatory restoration visible in the same coordinator.
def exercise(args: argparse.Namespace, report: dict, targets: list | None = None) -> None:  # pylint: disable=too-many-statements
    """Apply, launch, test, close and restore only a confined original fixture."""
    context = trial_context(args.trial)
    executable = Path(context["installation"]).parent.parent / "Aside.exe"
    if browser_processes(executable):
        raise ValueError("Stop the isolated fixture before starting automation")
    initial = dispatch(dict(context, operation="inspect"))
    if initial["current"]:
        raise ValueError("Automation starts from verified originals; restore this fixture first")
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    suite = suite_for_targets(suite, suite["targets"] if targets is None else targets)
    validate_suite(suite, initial["version"])
    if not args.routed_input:
        raise ValueError("Desktop acceptance requires --routed-input on a dedicated interactive test desktop")
    if args.native_port == args.cdp_port:
        raise ValueError("Native and content protocols need different ports")
    for port in (args.native_port, args.cdp_port):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    profile = args.trial.resolve() / f"automation-profile-{uuid.uuid4().hex}"
    seed_profile(profile)
    if args.bookmarks:
        bookmarks = json.loads(args.bookmarks.read_text(encoding="utf-8"))
        (profile / "Default" / "Bookmarks").write_text(json.dumps(bookmarks), encoding="utf-8")
    context = dict(context, data_root=str(profile))
    report.update(
        browser_version=initial["version"], profile=str(profile), targets=suite["targets"], installation=context["installation"]
    )
    report["suite_sha256"] = hashlib.sha256(args.suite.read_bytes()).hexdigest()
    report["original_hashes"] = initial["hashes"]
    report["harness_sha256"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            Path(__file__),
            *(
                Path(__file__).with_name(name)
                for name in ("native_ui.py", "content_ui.py", "windows_input.py", "native_matrix.py", "read_native_menus.ps1")
            ),
        )
    }
    native = None
    try:
        preview = dispatch(dict(context, operation="preview", targets=suite["targets"]))
        installed = dispatch(dict(context, operation="apply", targets=suite["targets"], preview_token=preview["token"]))
        report["patched_hashes"] = installed["hashes"]
        native, browser = launch_fixture(executable, profile, args)
        observations = {"browser": browser, "cdp_port": args.cdp_port, "routed_input": args.routed_input, "executable": executable}
        try:
            ui = NativeUI(native)
            ui.wait(lambda: ui.find("AsideSectionHeaderButton", "Bookmarks"))
            with ExitStack() as resources:
                observations["resources"] = resources
                scenarios(ui, suite, Path(report["evidence_directory"]), report, observations)
        finally:
            browser.close()
        report["scenario_passed"] = all(case["passed"] for case in report["cases"])
        native.close()
        native = None
        close_browser(executable)
        checks = resolve_checks(suite.get("restart_checks", []), observations)
        if checks:
            report["restart_persistence_passed"] = False
            restart_checks(args, profile, checks, report)
            report["restart_persistence_passed"] = True
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError):
        report["scenario_passed"] = False
        raise
    finally:
        if native:
            native.close()
        close_browser(executable)
        if report["scenario_passed"]:
            record_profile_storage(profile, suite, report)
        preview = dispatch(dict(context, operation="preview", targets=[]))
        restored = dispatch(dict(context, operation="apply", targets=[], preview_token=preview["token"]))
        report["restored"] = restored["hashes"] == initial["hashes"]
        if not report["restored"]:
            raise ValueError("Original fixture hashes were not restored")
        if report["scenario_passed"] and suite.get("restored_checks"):
            report["restored_ui_passed"] = False
            checks = resolve_checks(suite["restored_checks"], observations)
            restart_checks(args, profile, checks, report, phase="restored")
            report["restored_ui_passed"] = True


def run_matrix(args: argparse.Namespace, report: dict) -> None:
    """Run every advertised selection; continue independent cases after safe restoration."""
    context = trial_context(args.trial)
    initial = dispatch(dict(context, operation="inspect"))
    if initial["current"]:
        raise ValueError("Combination acceptance must start with verified originals")
    if not initial["combinations"]:
        raise ValueError("No advertised combinations are available for this fixture")
    report.update(kind="native-scenario-matrix", combinations=[], combination_results=[])
    for index, targets in enumerate(initial["combinations"]):
        destination = Path(report["evidence_directory"]) / f"combination-{index:02d}"
        destination.mkdir()
        child = {
            "schema_version": 1,
            "runtime_passed": False,
            "scenario_passed": False,
            "evidence_directory": str(destination),
            "cases": [],
            "targets": list(targets),
        }
        try:
            exercise(args, child, list(targets))
        except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as error:
            child.update(scenario_passed=False, error=str(error))
        if not child.get("restored"):
            after = dispatch(dict(context, operation="inspect"))
            journal = Path(context["installation"]).with_name(".aside-patcher-transaction.json")
            child["restored"] = not after["current"] and after["hashes"] == initial["hashes"] and not journal.exists()
        passed = child["scenario_passed"] and child["restored"]
        report["combinations"].append({"targets": list(targets), "passed": passed})
        report["combination_results"].append(child)
        (destination / "report.json").write_text(json.dumps(child, indent=2) + "\n", encoding="utf-8")
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if not child["restored"]:
            raise ValueError("Fixture restoration failed; remaining combinations were not started")
    report["scenario_passed"] = all(item["passed"] for item in report["combinations"])
    report["restored"] = all(item["restored"] for item in report["combination_results"])


def main() -> int:
    """Write honest coverage and cleanup results even after an assertion fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", required=True, type=Path)
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--bookmarks", type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--cdp-port", type=int, default=9337)
    parser.add_argument("--native-port", type=int, default=9338)
    parser.add_argument("--routed-input", action="store_true", help="Allow scoped real input on a dedicated interactive test desktop")
    parser.add_argument(
        "--all-combinations", action="store_true", help="Exercise every selection advertised for this isolated fixture"
    )
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    evidence = args.report.parent / run_id
    evidence.mkdir()
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "running",
        "evidence_directory": str(evidence.resolve()),
        "runtime_passed": False,
        "scenario_passed": False,
        "cases": [],
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    try:
        if args.all_combinations:
            run_matrix(args, report)
        else:
            exercise(args, report)
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as error:
        report.update(scenario_passed=False, error=str(error))
    report["status"] = "finished"
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["scenario_passed"] and report.get("restored") else 2


if __name__ == "__main__":
    raise SystemExit(main())
