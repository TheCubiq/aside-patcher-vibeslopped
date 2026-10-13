# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
from dataclasses import replace
from unittest.mock import patch

from aside_patcher.cli import main
from aside_patcher.common.exceptions import Refusal
from aside_patcher.installation import run_installation
from aside_patcher.native_scope import native_only_build
from aside_patcher.recipes import render
from tests.fixtures import TestInstallationFixture


class TestNativeScope(TestInstallationFixture):
    def test_missing_settings_does_not_block_guarded_native_change(self):
        build = native_only_build(self.build)
        self.ui.unlink()
        run_installation(build, "enable", ["ask-aside"])
        self.assertEqual(self.dll.read_bytes(), render(self.original["dll"], self.files["dll"], ("ask-aside",)))
        self.assertFalse(self.ui.exists())

    def test_unknown_native_hash_still_refuses(self):
        build = native_only_build(self.build)
        self.dll.write_bytes(b"unknown")
        with self.assertRaises(Refusal):
            run_installation(build, "enable", ["ask-aside"])
        self.assertFalse(self.backups.exists())

    def test_ambiguous_native_states_refuse(self):
        states = {(): {"dll": "same"}, ("ask-aside",): {"dll": "same"}}
        with self.assertRaises(Refusal):
            native_only_build(replace(self.build, states=states))

    def test_cli_refuses_settings_patch_in_native_only_mode(self):
        with patch("aside_patcher.cli.load_build", return_value=self.build), patch("aside_patcher.cli.run_installation") as install:
            self.assertEqual(main(["--native-only", "enable", "chats-toggle"]), 2)
        install.assert_not_called()
