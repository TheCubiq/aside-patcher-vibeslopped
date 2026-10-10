# Test names describe assertions; fixtures live until unittest addCleanup.
# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
# pylint: disable=too-many-instance-attributes,consider-using-with
import unittest
from dataclasses import replace
from unittest.mock import patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.models import Artifact
from aside_patcher.common.utils import sha256
from aside_patcher.installation import run_installation
from aside_patcher.recipes import render
from tests.fixtures import TestInstallationFixture


class TestInstallation(TestInstallationFixture):
    def setUp(self):
        super().setUp()
        backup_path = patch("aside_patcher.installation.backup_directory", return_value=self.backups)
        backup_path.start()
        self.addCleanup(backup_path.stop)

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
        self.ui.write_bytes(render(self.original["appearance"], self.files["appearance"], ("chats-toggle",)))
        with self.assertRaisesRegex(Refusal, "inconsistent"):
            run_installation(self.build, "status")

    def test_ambiguous_match_refuses(self):
        artifact = Artifact(
            self.ui,
            sha256(b"xx xx"),
            {"chats-toggle": {"sites": [{"offset": 0, "before": "7878", "after": "7979"}]}},
        )
        with self.assertRaises(Refusal):
            render(b"xx xx", artifact, ("chats-toggle",))

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

    def test_overlapping_signature_matches_refuse(self):
        artifact = Artifact(
            self.ui,
            sha256(b"aaaa"),
            {"chats-toggle": {"sites": [{"offset": 0, "before": b"aaa".hex(), "after": b"bbb".hex()}]}},
        )
        with self.assertRaises(Refusal):
            render(b"aaaa", artifact, ("chats-toggle",))

    def test_unverified_result_hash_refuses_without_replacement(self):
        states = dict(self.build.states)
        states[("chats-toggle",)] = {"dll": "incorrect", "appearance": "incorrect"}
        with self.assertRaises(Refusal):
            run_installation(replace(self.build, states=states), "apply", ["chats-toggle"])
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])
        self.assertEqual(self.ui.read_bytes(), self.original["appearance"])

    def test_updated_settings_component_refuses(self):
        component = self.root / "components"
        (component / "1.0").mkdir(parents=True)
        (component / "2.0").mkdir()
        build = replace(self.build, component={"directory": str(component), "version": "1.0"})
        with self.assertRaisesRegex(Refusal, "component version"):
            run_installation(build, "apply", ["chats-toggle"])
        self.assertFalse(self.backups.exists())


if __name__ == "__main__":
    unittest.main()
