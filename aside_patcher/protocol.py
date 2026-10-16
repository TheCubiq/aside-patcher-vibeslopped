"""Versioned JSON engine interface used by the unelevated desktop frontend."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from .common.exceptions import Refusal
from .common.models import MANIFEST_PATH, Installation
from .common.utils import backup_directory, file_sha256, sha256
from .compatibility import bundled_build, cached_build, refresh
from .discovery import discover, resolve_installation
from .patches import PATCHES
from .recipes import prepare_changes, validate_component_change
from .recovery import recover
from .transaction import replace_transaction
from .windows import (
    browser_processes,
    close_browser,
    elevate,
    installation_lock,
    is_admin,
    launch_arguments,
    profile_data_root,
    require_browser_stopped,
)
from .windows_security import helper_result

PROTOCOL_VERSION = 1


def inspect_build(build: Installation, native_only: bool = False) -> dict:
    """Describe verified installed state and available selections.

    Parameters
    ----------
    build : Installation
        Discovered and scoped reviewed build.
    native_only : bool
        Whether settings compatibility is unavailable.

    Returns
    -------
    dict
        Hashes, active IDs, legal combinations, and patch availability.
    """
    hashes = {name: file_sha256(artifact.path) for name, artifact in build.files.items()}
    current = build.identify(hashes)
    definitions = {patch.patch_id: patch for patch in PATCHES}
    definitions.update(build.patches)
    patches = [_patch_row(patch, build, current, definitions, native_only) for patch in definitions.values()]
    editable = {patch["id"] for patch in patches if patch["editable"]}
    combinations = [
        targets
        for targets in build.states
        if set(targets) - set(current) <= editable and (not native_only or set(targets).symmetric_difference(current) <= editable)
    ]
    return {
        "version": build.version,
        "current": current,
        "hashes": hashes,
        "files": {name: str(artifact.path) for name, artifact in build.files.items()},
        "patches": patches,
        "combinations": combinations,
        "native_only": native_only,
        "restore_supported": () in combinations,
    }


def _native_editable(patch, current, definitions):
    """Require independent DLL recipes without a locked settings conflict.

    Parameters
    ----------
    patch : PatchDefinition
        Patch being offered.
    current : tuple
        Installed IDs.
    definitions : dict
        Known patch dependencies.

    Returns
    -------
    bool
        Whether this patch can change independently of settings assets.
    """
    if set(patch.files) != {"dll"}:
        return False
    return not any(other in current and set(definitions[other].files) != {"dll"} for other in patch.conflicts if other in definitions)


def _patch_row(patch, build, current, definitions, native_only):
    """Describe one patch's actual editing capability.

    Parameters
    ----------
    patch : PatchDefinition
        Patch being offered.
    build : Installation
        Reviewed target combinations.
    current : tuple
        Installed IDs.
    definitions : dict
        Known dependencies and conflicts.
    native_only : bool
        Whether settings support is unavailable.

    Returns
    -------
    dict
        Display metadata and editing policy.
    """
    editable = patch.patch_id in build.targets and (not native_only or _native_editable(patch, current, definitions))
    metadata = build.patch_metadata.get(patch.patch_id, {})
    status = metadata.get("status", "supported" if editable else "unsupported")
    editable = editable and status == "supported"
    reason = metadata.get("reason", "Requires a verified browser/settings adapter.")
    diagnostics = ""
    if "analysis_passed" in metadata and not editable:
        diagnostics = reason
        reason = "Not available on this browser version." if status == "unsupported" else "Not yet verified for this browser version."
    return {
        "id": patch.patch_id,
        "description": patch.description,
        "conflicts": patch.conflicts,
        "installed": patch.patch_id in current,
        "editable": editable,
        "status": status,
        "reason": "" if editable else reason,
        "diagnostics": diagnostics,
        "official_replacement": metadata.get("official_replacement", ""),
    }


def preview_build(build: Installation, targets: list[str], native_only: bool = False) -> dict:
    """Render and verify a complete requested selection before browser shutdown.

    Parameters
    ----------
    build : Installation
        Reviewed discovered installation.
    targets : list of str
        Complete desired patch selection.
    native_only : bool
        Whether only independent native patches may change.

    Returns
    -------
    dict
        Stable preview identity, exact inputs, outputs, and affected paths.
    """
    _validate_targets(targets)
    state = inspect_build(build, native_only)
    desired = tuple(sorted(set(targets)))
    if desired not in build.states:
        raise Refusal("Requested selection has no verified output combination.")
    editable = {patch["id"] for patch in state["patches"] if patch["editable"]}
    if set(desired) - set(state["current"]) - editable:
        raise Refusal("Requested selection enables an unavailable or retired patch.")
    if native_only and set(desired).symmetric_difference(state["current"]) - editable:
        raise Refusal("Settings compatibility is unavailable; only independent native patches can change.")
    validate_component_change(build, state["hashes"], desired)
    changes = prepare_changes(build, state["hashes"], desired)
    preview = {
        "version": build.version,
        "current": state["current"],
        "desired": desired,
        "inputs": state["hashes"],
        "expected": build.states[desired],
        "files": state["files"],
        "affected": [str(path) for path in changes],
        "native_only": native_only,
    }
    preview["token"] = sha256(json.dumps(preview, sort_keys=True).encode())
    preview["requires_elevation"] = any(not _writable(path.parent) for path in changes)
    return preview


def _writable(directory):
    """Probe directory ACLs once without Python's Windows tempfile retry loop.

    Parameters
    ----------
    directory : Path
        Artifact directory to probe.

    Returns
    -------
    bool
        Whether the caller can stage a replacement in this directory.
    """
    path = directory / (".aside-patcher-access-" + uuid.uuid4().hex)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except PermissionError:
        return False
    os.close(descriptor)
    path.unlink()
    return True


def _selected(request):
    """Require one discovered browser, retaining per-user paths across UAC.

    Parameters
    ----------
    request : dict
        Protocol request optionally selecting a discovered DLL.

    Returns
    -------
    dict
        Selected browser version, DLL, and executable paths.
    """
    entries = discover()
    chosen = request.get("installation")
    if chosen:
        # Per-user installs must remain discoverable under alternate UAC credentials.
        entries = discover([Path(chosen).resolve().parent.parent])
        entries = [entry for entry in entries if Path(entry["dll"]).resolve() == Path(chosen).resolve()]
    if len(entries) != 1:
        raise Refusal("Select exactly one detected Aside installation.")
    return entries[0]


def _resolve(request):
    """Resolve signed or bundled support against the initiating user's files.

    Parameters
    ----------
    request : dict
        Selected browser and initiating user context.

    Returns
    -------
    tuple
        Browser entry, data root, reviewed definition, and native-only flag.
    """
    item = _selected(request)
    default_data = Path(os.environ["LOCALAPPDATA"]) / "Aside" / "User Data"
    data = (
        Path(request["data_root"]).resolve()
        if request.get("data_root")
        else profile_data_root(launch_arguments(Path(item["executable"])), default_data)
    )
    native_only = False
    cache = Path(request.get("cache_root", str(backup_directory().parent / "compatibility")))
    adapter = _approved_adapter(item, cache)
    try:
        build = resolve_installation(MANIFEST_PATH, item, data, adapter=adapter)
        native_only = "appearance" not in build.files
    except Refusal:
        build = resolve_installation(MANIFEST_PATH, item, data, native_only=True, adapter=adapter)
        native_only = True
    return item, data, build, native_only


def _approved_adapter(item: dict, cache: Path) -> Installation | None:
    """Select cached support only when it recognizes the installed DLL.

    Parameters
    ----------
    item : dict
        Discovered browser; receives an actionable cache warning when needed.
    cache : Path
        Original user's compatibility cache.

    Returns
    -------
    Installation or None
        Verified matching adapter, or None to try independent bundled support.
    """
    try:
        adapter = cached_build(cache, item["version"])
    except (Refusal, OSError, ValueError, KeyError, TypeError) as error:
        item["compatibility_warning"] = f"Cached support rejected; using bundled definitions. {error}"
        adapter = None

    if adapter is None:
        adapter = bundled_build(item["version"])
    if adapter is None:
        return None

    native_hash = file_sha256(Path(item["dll"]))
    accepted_states = tuple(adapter.states.items()) + adapter.previous_states
    for _, hashes in accepted_states:
        if hashes["dll"] == native_hash:
            return adapter

    # A narrower catalog package must not hide a recognized bundled installation.
    return None


def _execute(request, progress):
    """Recover or install under an exclusive installation lock.

    Parameters
    ----------
    request : dict
        Revalidated mutation ticket bound to a preview.
    progress : callable
        Callback receiving a stage and user-facing message.

    Returns
    -------
    dict
        Verified resulting installation state.
    """
    item, _, build, native_only = _resolve(request)
    journal = Path(item["dll"]).parent / ".aside-patcher-transaction.json"
    with installation_lock(Path(item["dll"]).parent):
        require_browser_stopped(Path(item["executable"]))
        if request.get("operation") == "recover":
            restored = recover(build, journal)
            return dict(inspect_build(build, native_only), recovered=restored)
        if recover(build, journal):
            raise Refusal("Interrupted operation recovered. Inspect and preview the restored state before retrying.")
        preview = preview_build(build, request["targets"], native_only)
        if preview["token"] != request.get("preview_token"):
            raise Refusal("Installation or selection changed after preview. Review a new preview.")
        progress("installing", "Installing verified files")
        hashes = {name: file_sha256(artifact.path) for name, artifact in build.files.items()}
        changes = prepare_changes(build, hashes, tuple(preview["desired"]))
        replace_transaction(changes, journal, guard=lambda: require_browser_stopped(Path(item["executable"])))
        result = inspect_build(build, native_only)
        result["changed"] = bool(changes)
        return result


def _progress(stage, message):
    """Emit one flushed protocol progress record.

    Parameters
    ----------
    stage : str
        Stable operation stage identifier.
    message : str
        User-facing progress description.
    """
    print(json.dumps({"protocol_version": PROTOCOL_VERSION, "type": "progress", "stage": stage, "message": message}), flush=True)


def dispatch(request: dict) -> dict:
    """Dispatch one bounded protocol request from the original user's process.

    Parameters
    ----------
    request : dict
        Versioned inspect, preview, apply, or restore request.

    Returns
    -------
    dict
        Structured operation result.
    """
    _validate_request(request)
    operation = request.get("operation")
    if operation == "refresh":
        try:
            return refresh(backup_directory().parent / "compatibility")
        except (OSError, Refusal) as exc:
            return {"message": f"Compatibility refresh failed; existing support retained. {exc}", "offline": True}
    if operation == "inspect" and not request.get("installation"):
        entries = discover()
        return {"installations": entries} if len(entries) != 1 else _inspect_selected(dict(request, installation=entries[0]["dll"]))
    if operation == "inspect":
        return _inspect_selected(request)
    if operation == "preview":
        _, _, build, native_only = _resolve(request)
        return preview_build(build, request["targets"], native_only)
    if operation not in ("apply", "restore", "recover"):
        raise Refusal("Unknown protocol operation.")
    return _apply_request(request, operation)


def _validate_request(request):
    """Validate the JSON boundary before discovery or process operations.

    Parameters
    ----------
    request : dict
        Untrusted protocol document.
    """
    if not isinstance(request, dict):
        raise Refusal("Protocol request must be a JSON object.")
    if request.get("protocol_version") != PROTOCOL_VERSION:
        raise Refusal("Unsupported engine protocol version.")
    if request.get("operation") in ("apply", "restore", "recover") and is_admin():
        raise Refusal("Run the desktop app without administrator privileges. Only its installation helper may elevate.")


def _validate_targets(targets):
    """Reject non-list and non-string selections before state resolution.

    Parameters
    ----------
    targets : list of str
        Complete desired patch selection.
    """
    if not isinstance(targets, list) or not all(isinstance(target, str) for target in targets):
        raise Refusal("Targets must be a list of patch identifiers.")


def _apply_request(request, operation):
    """Coordinate shutdown and UAC while restarting only from the original user.

    Parameters
    ----------
    request : dict
        Requested complete selection and preview token.
    operation : str
        Apply, restore, or recovery operation.

    Returns
    -------
    dict
        Verified resulting state and restart outcome.
    """
    item, data, build, native_only = _resolve(request)
    if operation == "restore":
        request = dict(request, targets=[])
    if operation == "recover":
        preview = {
            "affected": [str(artifact.path) for artifact in build.files.values()],
            "requires_elevation": not _writable(Path(item["dll"]).parent),
        }
    else:
        preview = preview_build(build, request["targets"], native_only)
        if preview["token"] != request.get("preview_token"):
            raise Refusal("Installation changed after preview. Review the new state before applying.")
    if not preview["affected"]:
        return dict(inspect_build(build, native_only), changed=False, restarted=False)
    _progress("closing", "Closing Aside gracefully")
    arguments = launch_arguments(Path(item["executable"]))
    was_running = close_browser(Path(item["executable"]))
    request = dict(request, data_root=str(data), installation=item["dll"])
    # Bind the original user's backup path before UAC can change accounts.
    request["backup_root"] = str(backup_directory())
    request["cache_root"] = str(backup_directory().parent / "compatibility")
    try:
        result = _run_mutation(request, preview)
    except (OSError, Refusal):
        if was_running:
            subprocess.Popen(  # pylint: disable=consider-using-with
                [item["executable"], *arguments], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        raise
    if was_running:
        _finish_restart(request, preview, item, arguments)
    return dict(result, restarted=was_running)


def _finish_restart(request, preview, item, arguments):
    """Restore the previous selection if a failed launch leaves no live browser.

    Parameters
    ----------
    request : dict
        Original user's bound mutation context.
    preview : dict
        Verified selection before installation.
    item : dict
        Selected browser launcher and installation.
    arguments : list of str
        Original main-process arguments.
    """
    try:
        _restart_browser(item["executable"], arguments)
        return
    except (OSError, Refusal) as failure:
        if "current" not in preview:
            raise Refusal(f"Recovery completed, but browser restart failed: {failure}") from failure
        try:
            require_browser_stopped(Path(item["executable"]))
            _, _, build, native_only = _resolve(request)
            previous = preview_build(build, list(preview["current"]), native_only)
            rollback = dict(request, operation="apply", targets=list(preview["current"]), preview_token=previous["token"])
            _progress("restoring", "Browser launch failed; restoring previous selection")
            _run_mutation(rollback, previous)
            _restart_browser(item["executable"], arguments)
        except (OSError, Refusal) as recovery_failure:
            raise Refusal(
                f"Browser restart failed: {failure}. Automatic restoration could not finish: {recovery_failure}"
            ) from failure
        raise Refusal("Browser launch failed. Previous patch selection was restored and the browser restarted.") from failure


def _restart_browser(executable, arguments):
    """Restart from the unelevated caller with the captured profile arguments.

    Parameters
    ----------
    executable : str
        Exact selected browser launcher.
    arguments : list of str
        Captured original main-process arguments.
    """
    _progress("restarting", "Restarting Aside")
    process = subprocess.Popen([executable, *arguments])  # pylint: disable=consider-using-with
    time.sleep(2)
    if process.poll() not in (None, 0) or not browser_processes(Path(executable)):
        raise Refusal("Installed hashes verified, but browser launch failed. Use Restore originals.")


def _run_mutation(request, preview):
    """Invoke only the protected mutation in a separate elevated engine process.

    Parameters
    ----------
    request : dict
        Revalidated request bound to the original user context.
    preview : dict
        Verified preview and elevation requirement.

    Returns
    -------
    dict
        Verified installed state returned by the worker.
    """
    if not preview["requires_elevation"] or is_admin():
        return _execute(request, _progress)
    _progress("elevating", "Waiting for Windows approval")
    with tempfile.TemporaryDirectory(prefix="aside-patcher-") as temporary:
        request_path, result_path = Path(temporary) / "request.json", Path(temporary) / "result.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        arguments = ["--helper", str(request_path), str(result_path)]
        if not getattr(sys, "frozen", False):
            arguments = ["-m", "aside_patcher.protocol", *arguments]
        code = elevate(arguments, Path(__file__).resolve().parent.parent)
        if not result_path.exists():
            raise Refusal(f"Elevated helper did not return a result (exit {code}).")
        envelope = json.loads(result_path.read_text(encoding="utf-8"))
        if code or not envelope.get("ok"):
            raise Refusal(envelope.get("error", "Elevated installation failed."), code=envelope.get("code", "refused"))
        return envelope["result"]


def _inspect_selected(request):
    """Describe unsupported or recoverable installations without enabling writes.

    Parameters
    ----------
    request : dict
        Selected installation request.

    Returns
    -------
    dict
        Verified state or an actionable refusal description.
    """
    item = _selected(request)
    try:
        if (Path(item["dll"]).parent / ".aside-patcher-transaction.json").exists():
            raise Refusal("An interrupted installation needs recovery before patch selection.")
        item, data, build, native_only = _resolve(request)
        return dict(
            inspect_build(build, native_only),
            installation=item,
            installations=discover(),
            data_root=str(data),
            compatibility_warning=item.get("compatibility_warning"),
        )
    except Refusal as exc:
        return {
            "installation": item,
            "installations": discover(),
            "patches": [],
            "supported": False,
            "recovery_required": (Path(item["dll"]).parent / ".aside-patcher-transaction.json").exists(),
            "reason": str(exc),
        }


def _execute_helper(source: Path) -> dict:
    """Validate an internal ticket and use the initiating user's backup directory.

    Parameters
    ----------
    source : Path
        Internal request ticket beside the established result channel.

    Returns
    -------
    dict
        Revalidated mutation result.
    """
    from aside_patcher import recipes  # pylint: disable=import-outside-toplevel

    request = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise Refusal("Helper request must be a JSON object.")
    if request.get("protocol_version") != PROTOCOL_VERSION or request.get("operation") not in ("apply", "restore", "recover"):
        raise Refusal("Invalid helper operation.")
    recipes.backup_directory = lambda: Path(request["backup_root"])
    return _execute(request, lambda *_: None)


def main() -> int:
    """Read one JSON request and emit progress followed by a result envelope.

    Returns
    -------
    int
        Zero on success, two on a validated refusal.
    """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", nargs=2, metavar=("REQUEST", "RESULT"))
    args = parser.parse_args()
    with ExitStack() as outputs:
        result_stream = None
        try:
            if args.helper:
                source, target = map(Path, args.helper)
                if source.parent.resolve() != target.parent.resolve() or source.name != "request.json" or target.name != "result.json":
                    raise Refusal("Invalid helper ticket paths.")
                # Establish the return channel before any privileged mutation.
                result_stream = outputs.enter_context(helper_result(source, target))
                result = _execute_helper(source)
            else:
                request = json.loads(sys.stdin.readline(1024 * 1024))
                result = dispatch(request)
            envelope = {"protocol_version": PROTOCOL_VERSION, "type": "result", "ok": True, "result": result}
        except (Refusal, OSError, ValueError, KeyError, TypeError) as exc:
            envelope = {
                "protocol_version": PROTOCOL_VERSION,
                "type": "result",
                "ok": False,
                "code": getattr(exc, "code", "refused"),
                "error": str(exc),
            }
        if result_stream is not None:
            json.dump(envelope, result_stream)
        elif args.helper:
            print(json.dumps(envelope), file=sys.stderr, flush=True)
        else:
            print(json.dumps(envelope), flush=True)
    return 0 if envelope["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
