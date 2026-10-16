"""Verify complete-state previews refuse unsafe or stale selections."""

from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher import protocol
from aside_patcher.discovery import discover
from aside_patcher.protocol import dispatch, inspect_build, preview_build
from aside_patcher.native_scope import native_only_build
from aside_patcher.recipes import render
from tests.fixtures import TestInstallationFixture


class TestProtocol(TestInstallationFixture):
    """Use synthetic artifacts; never close or modify the installed browser."""

    def test_frozen_helper_uses_executable_arguments(self):
        """A standalone engine is relaunched directly, without Python's -m flag."""
        calls = []
        for frozen in (False, True):
            calls.clear()

            def worker(arguments, _directory):
                calls.append(arguments)
                Path(arguments[-1]).write_text(json.dumps({"ok": True, "result": {"current": []}}), encoding="utf-8")
                return 0

            with (
                self.subTest(frozen=frozen),
                patch.object(protocol.sys, "frozen", frozen, create=True),
                patch.object(protocol, "is_admin", return_value=False),
                patch.object(protocol, "elevate", side_effect=worker),
            ):
                result = protocol._run_mutation({}, {"requires_elevation": True})  # pylint: disable=protected-access
                self.assertEqual(result, {"current": []})
                self.assertEqual(calls[0][0], "--helper" if frozen else "-m")

    def test_preview_checks_outputs_without_installing(self):
        """Render both artifacts but leave original installed bytes intact."""
        preview = preview_build(self.build, ["chats-toggle"])
        self.assertEqual(set(preview["affected"]), {str(self.dll), str(self.ui)})
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])
        self.assertEqual(self.ui.read_bytes(), self.original["appearance"])
        self.assertEqual(preview["expected"], self.build.states[("chats-toggle",)])

    def test_invalid_protocol_shapes_are_refused(self):
        """Reject malformed requests and selections before inspecting any files."""
        for request in (None, [], "inspect", 1):
            with self.subTest(request=request), self.assertRaises(Refusal):
                dispatch(request)
        for targets in ("chats", None, [1], {"chats": True}):
            with self.subTest(targets=targets), self.assertRaises(Refusal):
                preview_build(self.build, targets)

    def test_main_mutation_process_must_stay_unelevated(self):
        """Reject an elevated main process before shutdown or installation discovery."""
        with patch("aside_patcher.protocol.is_admin", return_value=True), patch("aside_patcher.protocol.close_browser") as close:
            with self.assertRaisesRegex(Refusal, "without administrator"):
                dispatch({"protocol_version": 1, "operation": "apply", "targets": []})
            close.assert_not_called()

    def test_elevation_does_not_change_preview_identity(self):
        """The same operation must retain its identity across a UAC token change."""
        with patch("aside_patcher.protocol._writable", return_value=True):
            unelevated = preview_build(self.build, ["chats"])
        with patch("aside_patcher.protocol._writable", return_value=False):
            elevated = preview_build(self.build, ["chats"])
        self.assertEqual(unelevated["token"], elevated["token"])

    def test_native_scope_cannot_change_settings_dependent_selection(self):
        """Missing settings support must not silently enable a partial toggle."""
        with self.assertRaises(Refusal):
            preview_build(self.build, ["chats-toggle"], native_only=True)

    def test_native_scope_advertises_only_choices_that_can_be_applied(self):
        """Recognition states for settings patches are not editable combinations."""
        build = native_only_build(self.build)
        state = inspect_build(build, native_only=True)
        for targets in state["combinations"]:
            self.assertNotIn("chats-toggle", targets)
            preview_build(build, list(targets), native_only=True)
        self.assertEqual(len(state["combinations"]), 4)
        self.assertTrue(state["restore_supported"])

    def test_native_restore_stays_locked_for_an_installed_settings_patch(self):
        """Restoration may not silently remove an unsupported settings dependency."""
        self.dll.write_bytes(render(self.original["dll"], self.files["dll"], ("chats-toggle",)))
        build = native_only_build(self.build)
        state = inspect_build(build, native_only=True)
        self.assertFalse(state["restore_supported"])
        self.assertNotIn((), state["combinations"])
        with self.assertRaisesRegex(Refusal, "only independent native patches"):
            preview_build(build, [], native_only=True)

    def test_invalid_online_cache_does_not_disable_verified_bundled_support(self):
        """Reject cached update data while retaining independent bundled recipes."""
        item = {"version": "test", "dll": str(self.dll), "executable": str(self.root / "Aside.exe")}
        with (
            patch("aside_patcher.protocol._selected", return_value=item),
            patch("aside_patcher.protocol.cached_build", side_effect=Refusal("Invalid cached signature")),
            patch("aside_patcher.protocol.resolve_installation", return_value=self.build),
        ):
            resolved = protocol._resolve({})  # pylint: disable=protected-access
        self.assertIs(resolved[2], self.build)
        self.assertIn("Invalid cached signature", resolved[0]["compatibility_warning"])

    def test_multiple_custom_install_roots_are_discovered(self):
        """Detect versioned installations independent of account and drive."""
        (self.root / "Aside.exe").write_bytes(b"synthetic launcher")
        folder = self.root / "1.2.3.4"
        folder.mkdir()
        (folder / "Aside.dll").write_bytes(b"synthetic image")
        self.assertEqual(discover([self.root])[0]["version"], "1.2.3.4")

    def test_changed_input_invalidates_preview(self):
        """An unknown changed artifact cannot reuse an earlier valid preview."""
        preview_build(self.build, ["chats"])
        self.dll.write_bytes(b"changed after preview")
        with self.assertRaises(Refusal):
            preview_build(self.build, ["chats"])

    def test_retired_patch_explains_replacement_and_cannot_be_enabled(self):
        """Reviewed retirement must disable the checkbox and engine operation."""
        build = replace(
            self.build,
            patch_metadata={
                "chats": {
                    "status": "retired",
                    "reason": "Official setting added",
                    "official_replacement": "Settings > Sidebar",
                }
            },
        )
        row = next(item for item in inspect_build(build)["patches"] if item["id"] == "chats")
        self.assertFalse(row["editable"])
        self.assertEqual(row["official_replacement"], "Settings > Sidebar")
        with self.assertRaisesRegex(Refusal, "unavailable or retired"):
            preview_build(build, ["chats"])

    def test_failed_restart_restores_previous_selection_only_when_stopped(self):
        """Rollback a failed launch, but never replace files under a live browser."""
        item = {"executable": str(self.root / "Aside.exe")}
        for stopped in (True, False):
            with (
                self.subTest(stopped=stopped),
                patch("aside_patcher.protocol._restart_browser", side_effect=[Refusal("launch failed"), None]),
                patch("aside_patcher.protocol.require_browser_stopped", side_effect=None if stopped else Refusal("still running")),
                patch("aside_patcher.protocol._resolve", return_value=(item, self.root, self.build, False)),
                patch("aside_patcher.protocol._run_mutation") as mutation,
            ):
                with self.assertRaises(Refusal):
                    protocol._finish_restart({}, {"current": []}, item, ["--user-data-dir=trial"])  # pylint: disable=protected-access
                if stopped:
                    self.assertEqual(mutation.call_args.args[0]["targets"], [])
                    self.assertEqual(mutation.call_args.args[0]["operation"], "apply")
                else:
                    mutation.assert_not_called()
