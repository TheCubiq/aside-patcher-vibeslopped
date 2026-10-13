# Test names describe assertions; fixtures live until unittest addCleanup.
# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
# pylint: disable=too-many-instance-attributes,consider-using-with
from dataclasses import replace
from unittest.mock import patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.models import Artifact
from aside_patcher.common.utils import sha256
from aside_patcher.installation import run_installation
from aside_patcher.recipes import render
from tests.fixtures import TestInstallationFixture


class TestRefusals(TestInstallationFixture):
    def test_disable_refuses_invalid_requests_and_unverified_state(self):
        for targets in ([], ["unknown"]):
            with self.assertRaises(Refusal):
                run_installation(self.build, "disable", targets)
        states = dict(self.build.states)
        del states[("ask-aside",)]
        build = replace(self.build, states=states)
        with self.assertRaises(Refusal):
            build.choose(("ask-aside", "chats-toggle"), ["chats-toggle"], enable=False)
        self.assertFalse(self.backups.exists())

    def test_conflicting_selection_refuses(self):
        with self.assertRaises(Refusal):
            run_installation(self.build, "enable", ["chats", "chats-toggle"])
        self.assertFalse(self.backups.exists())

    def test_unknown_hash_refuses_before_backups(self):
        self.ui.write_bytes(b"unknown")
        with self.assertRaises(Refusal):
            run_installation(self.build, "enable", ["chats-toggle"])
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
                run_installation(self.build, "enable", ["chats-toggle"])
            read.assert_not_called()

    def test_unknown_target_refuses(self):
        with self.assertRaises(Refusal):
            run_installation(self.build, "enable", ["unknown"])

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
            run_installation(replace(self.build, states=states), "enable", ["chats-toggle"])
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])
        self.assertEqual(self.ui.read_bytes(), self.original["appearance"])

    def updated_component(self) -> dict:
        component = self.root / "components"
        (component / "1.0").mkdir(parents=True)
        (component / "2.0").mkdir()
        return {"directory": str(component), "version": "1.0"}

    def test_updated_settings_component_refuses(self):
        build = replace(self.build, component=self.updated_component())
        with self.assertRaisesRegex(Refusal, "component version"):
            run_installation(build, "enable", ["chats-toggle"])
        self.assertFalse(self.backups.exists())

    def test_native_only_change_preserves_updated_settings_component(self):
        build = replace(self.build, component=dict(self.updated_component(), file="appearance"))
        self.assertEqual(run_installation(build, "enable", ["ask-aside"]), 0)
        self.assertEqual(self.ui.read_bytes(), self.original["appearance"])
        with self.assertRaisesRegex(Refusal, "component version"):
            run_installation(build, "enable", ["chats-toggle"])
