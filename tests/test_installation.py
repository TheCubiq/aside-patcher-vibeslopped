# Test fixtures live until addCleanup runs after each test.
# pylint: disable=too-many-instance-attributes,consider-using-with
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from aside_patcher.core import Refusal, sha256
from aside_patcher.installation import (
    Artifact,
    Installation,
    preserve_original,
    render,
    replace_transaction,
    run_installation,
)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dll = self.root / "Aside.dll"
        self.ui = self.root / "appearance.js"
        self.dll.write_bytes(b"DLL chats off ask on")
        self.ui.write_bytes(b"UI chats disabled")
        self.original = {
            "dll": self.dll.read_bytes(),
            "appearance": self.ui.read_bytes(),
        }
        self.files = {
            "dll": Artifact(
                self.dll,
                sha256(self.original["dll"]),
                {
                    "chats": {
                        "sites": [
                            {
                                "offset": 4,
                                "before": b"chats off".hex(),
                                "after": b"chats hid".hex(),
                            }
                        ]
                    },
                    "chats-toggle": {
                        "sites": [
                            {
                                "offset": 4,
                                "before": b"chats off".hex(),
                                "after": b"chats toggle".hex(),
                            }
                        ]
                    },
                    "ask-aside": {
                        "sites": [
                            {
                                "offset": 14,
                                "before": b"ask on".hex(),
                                "after": b"ask no".hex(),
                            }
                        ]
                    },
                },
            ),
            "appearance": Artifact(
                self.ui,
                sha256(self.original["appearance"]),
                {
                    "chats-toggle": {
                        "sites": [
                            {
                                "offset": 9,
                                "before": b"disabled".hex(),
                                "after": b"enabled".hex(),
                            }
                        ]
                    }
                },
            ),
        }
        combinations = [
            (),
            ("chats",),
            ("ask-aside",),
            ("chats-toggle",),
            ("ask-aside", "chats"),
            ("ask-aside", "chats-toggle"),
        ]
        states = {
            state: {
                name: sha256(render(self.original[name], file, state))
                for name, file in self.files.items()
            }
            for state in combinations
        }
        self.build = Installation(
            "test",
            self.files,
            states,
            (("chats", "chats-toggle"),),
            ("chats", "ask-aside", "chats-toggle"),
        )
        self.backups = self.root / "backups"
        self.stopped = patch("aside_patcher.installation.require_app_stopped")
        self.stopped.start()
        self.addCleanup(self.stopped.stop)
        self.backup_mock = patch(
            "aside_patcher.installation.backup_directory", return_value=self.backups
        )
        self.backup_mock.start()
        self.addCleanup(self.backup_mock.stop)
        self.addCleanup(self.make_backups_writable)

    def make_backups_writable(self):
        if self.backups.exists():
            for p in self.backups.iterdir():
                p.chmod(0o644)

    def test_migrates_hide_to_toggle_preserving_ask(self):
        run_installation(self.build, "apply", ["chats", "ask-aside"])
        run_installation(self.build, "apply", ["chats-toggle"])
        self.assertEqual(self.dll.read_bytes(), b"DLL chats toggle ask no")
        self.assertEqual(self.ui.read_bytes(), b"UI chats enabled")
        run_installation(self.build, "apply", ["chats-toggle"])
        run_installation(self.build, "apply", ["chats"])
        self.assertEqual(self.dll.read_bytes(), b"DLL chats hid ask no")
        self.assertEqual(self.ui.read_bytes(), self.original["appearance"])

    def test_restore_is_byte_for_byte(self):
        run_installation(self.build, "apply", ["chats-toggle", "ask-aside"])
        run_installation(self.build, "restore")
        for name, f in self.files.items():
            self.assertEqual(f.path.read_bytes(), self.original[name])

    def test_conflicting_selection_refuses(self):
        with self.assertRaises(Refusal):
            run_installation(self.build, "apply", ["chats", "chats-toggle"])
        self.assertFalse(self.backups.exists())

    def test_unknown_hash_refuses_before_backups(self):
        self.ui.write_bytes(b"unknown")
        with self.assertRaises(Refusal):
            run_installation(self.build, "apply", ["chats-toggle"])
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])
        self.assertFalse(self.backups.exists())

    def test_known_but_inconsistent_combination_refuses(self):
        self.ui.write_bytes(
            render(
                self.original["appearance"], self.files["appearance"], ("chats-toggle",)
            )
        )
        with self.assertRaisesRegex(Refusal, "inconsistent"):
            run_installation(self.build, "status")

    def test_ambiguous_match_refuses(self):
        artifact = Artifact(
            self.ui,
            sha256(b"xx xx"),
            {
                "chats-toggle": {
                    "sites": [{"offset": 0, "before": "7878", "after": "7979"}]
                }
            },
        )
        with self.assertRaises(Refusal):
            render(b"xx xx", artifact, ("chats-toggle",))

    def test_original_backup_cannot_be_overwritten(self):
        f = self.files["dll"]
        backup = preserve_original(self.original["dll"], f, self.backups)
        self.assertEqual(
            preserve_original(self.original["dll"], f, self.backups), backup
        )
        backup.chmod(0o644)
        backup.write_bytes(b"corrupt")
        with self.assertRaises(Refusal):
            preserve_original(self.original["dll"], f, self.backups)
        self.assertEqual(backup.read_bytes(), b"corrupt")

    def test_all_replacements_staged_before_permission_failure(self):
        changes = {
            self.dll: (self.original["dll"], b"new DLL"),
            self.ui: (self.original["appearance"], b"new UI"),
        }
        from aside_patcher.installation import _stage

        def fail_ui(path, data, label):
            if path == self.ui:
                raise PermissionError("denied")
            return _stage(path, data, label)

        with (
            patch("aside_patcher.installation._stage", side_effect=fail_ui),
            self.assertRaises(Refusal),
        ):
            replace_transaction(changes)
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])
        self.assertEqual(self.ui.read_bytes(), self.original["appearance"])
        self.assertFalse(list(self.root.glob(".*")))

    def test_second_replacement_failure_rolls_back_first(self):
        changes = {
            self.dll: (self.original["dll"], b"new DLL"),
            self.ui: (self.original["appearance"], b"new UI"),
        }
        real_replace = os.replace

        def fail_ui(src, dst):
            if dst == self.ui and str(src).endswith(".new"):
                raise PermissionError("denied")
            real_replace(src, dst)

        with (
            patch("aside_patcher.installation.os.replace", side_effect=fail_ui),
            self.assertRaises(Refusal),
        ):
            replace_transaction(changes)
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])
        self.assertEqual(self.ui.read_bytes(), self.original["appearance"])
        self.assertFalse(list(self.root.glob(".*")))

    def test_running_aside_refuses_before_reading_files(self):
        with patch(
            "aside_patcher.installation.require_app_stopped",
            side_effect=Refusal("running"),
        ):
            with (
                patch("aside_patcher.installation.file_sha256") as read,
                self.assertRaises(Refusal),
            ):
                run_installation(self.build, "apply", ["chats-toggle"])
            read.assert_not_called()

    def test_unknown_target_refuses(self):
        with self.assertRaises(Refusal):
            run_installation(self.build, "apply", ["unknown"])

    def test_changed_file_during_staging_refuses(self):
        changes = {self.dll: (self.original["dll"], b"new DLL")}
        with (
            patch("aside_patcher.installation.file_sha256", return_value="different"),
            self.assertRaises(Refusal),
        ):
            replace_transaction(changes)
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])

    def test_overlapping_signature_matches_refuse(self):
        artifact = Artifact(
            self.ui,
            sha256(b"aaaa"),
            {
                "chats-toggle": {
                    "sites": [
                        {"offset": 0, "before": b"aaa".hex(), "after": b"bbb".hex()}
                    ]
                }
            },
        )
        with self.assertRaises(Refusal):
            render(b"aaaa", artifact, ("chats-toggle",))

    def test_unverified_result_hash_refuses_without_replacement(self):
        states = dict(self.build.states)
        states[("chats-toggle",)] = {"dll": "incorrect", "appearance": "incorrect"}
        with self.assertRaises(Refusal):
            run_installation(
                replace(self.build, states=states), "apply", ["chats-toggle"]
            )
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])
        self.assertEqual(self.ui.read_bytes(), self.original["appearance"])

    def test_updated_settings_component_refuses(self):
        component = self.root / "components"
        (component / "1.0").mkdir(parents=True)
        (component / "2.0").mkdir()
        build = replace(
            self.build, component={"directory": str(component), "version": "1.0"}
        )
        with self.assertRaisesRegex(Refusal, "component version"):
            run_installation(build, "apply", ["chats-toggle"])
        self.assertFalse(self.backups.exists())

    def test_failed_backup_write_does_not_leave_corrupt_backup(self):
        with (
            patch(
                "aside_patcher.installation.os.fsync",
                side_effect=OSError("flush failed"),
            ),
            self.assertRaises(Refusal),
        ):
            preserve_original(self.original["dll"], self.files["dll"], self.backups)
        self.assertEqual(list(self.backups.iterdir()), [])

    def test_failed_rollback_keeps_recovery_copy(self):
        changes = {
            self.dll: (self.original["dll"], b"new DLL"),
            self.ui: (self.original["appearance"], b"new UI"),
        }
        real_replace = os.replace

        def fail_replace(src, dst):
            if dst == self.ui or str(src).endswith(".rollback"):
                raise PermissionError("denied")
            real_replace(src, dst)

        with (
            patch("aside_patcher.installation.os.replace", side_effect=fail_replace),
            self.assertRaisesRegex(Refusal, "recovery copy"),
        ):
            replace_transaction(changes)
        recovery = list(self.root.glob(".Aside.dll.*.rollback"))
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0].read_bytes(), self.original["dll"])

    def test_native_builder_refuses_unknown_original(self):
        from aside_patcher.native_toggle import build_native_toggle

        with self.assertRaises(Refusal):
            build_native_toggle(b"unknown DLL")


if __name__ == "__main__":
    unittest.main()
