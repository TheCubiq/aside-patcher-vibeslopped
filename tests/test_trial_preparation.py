"""Verify isolated trial preparation cannot overwrite profiles or escape archives."""

from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

from scripts.prepare_desktop_trial import prepare_trial
from tests.fixtures import TestInstallationFixture


class TestTrialPreparation(TestInstallationFixture):
    """Use synthetic reviewed artifacts rather than archived vendor binaries."""

    def setUp(self):
        """Arrange a versioned package with a matching synthetic browser image."""
        super().setUp()
        self.package = self.root / "archive" / "browsers" / self.build.version / "digest"
        application = self.package / "Application"
        (application / self.build.version).mkdir(parents=True)
        (application / "Aside.exe").write_bytes(b"synthetic launcher")
        (application / self.build.version / "Aside.dll").write_bytes(self.original["dll"])
        self.record = self.package / "browser.json"
        self.record.write_text(json.dumps({"verified": True, "dll": f"Application/{self.build.version}/Aside.dll"}))
        self.trial = self.root / "Trial With Spaces"
        self.adapter = replace(self.build, component={"version": "settings-test"})

    def test_requested_build_and_profile_remain_independent(self):
        """Copy only after verifying input identities and preserve archive bytes."""
        with (
            patch("scripts.prepare_desktop_trial.load_build", return_value=self.adapter) as load,
            patch("scripts.prepare_desktop_trial.validate_browser_images") as validate,
        ):
            request = prepare_trial(self.root / "archive", self.ui, self.trial, self.build.version)
        self.assertEqual(load.call_args.args[1], self.build.version)
        self.assertEqual(validate.call_args.args[0]["version"], self.build.version)
        self.assertTrue(Path(request["installation"]).resolve().is_relative_to(self.trial.resolve()))
        self.assertEqual(Path(request["installation"]).read_bytes(), self.original["dll"])
        copied = self.trial / "profile" / "AsideAgentManager" / "settings-test" / "assets" / self.ui.name
        copied.write_bytes(b"trial changes")
        self.assertEqual(self.ui.read_bytes(), self.original["appearance"])

    def test_native_only_trial_needs_no_settings_component(self):
        """Independent DLL patches must not require an unavailable settings asset."""
        with (
            patch("scripts.prepare_desktop_trial.load_build", return_value=self.adapter),
            patch("scripts.prepare_desktop_trial.validate_browser_images"),
        ):
            request = prepare_trial(self.root / "archive", None, self.trial, self.build.version)
        self.assertTrue(Path(request["installation"]).is_file())
        self.assertTrue(Path(request["data_root"]).is_dir())
        self.assertFalse((Path(request["data_root"]) / "AsideAgentManager").exists())

    def test_invalid_sources_and_existing_destination_are_untouched(self):
        """Refuse unsupported settings, archive path escapes, and reused profiles."""
        with (
            patch("scripts.prepare_desktop_trial.load_build", return_value=self.adapter),
            patch("scripts.prepare_desktop_trial.validate_browser_images"),
        ):
            self.ui.write_bytes(b"unsupported")
            with self.assertRaisesRegex(ValueError, "settings hash"):
                prepare_trial(self.root / "archive", self.ui, self.trial)
            self.assertFalse(self.trial.exists())
            self.record.write_text(json.dumps({"verified": True, "dll": str(self.dll)}))
            with self.assertRaisesRegex(ValueError, "escaped"):
                prepare_trial(self.root / "archive", self.ui, self.trial)
            self.trial.mkdir()
            marker = self.trial / "keep.txt"
            marker.write_text("existing profile")
            with self.assertRaisesRegex(ValueError, "new directory"):
                prepare_trial(self.root / "archive", self.ui, self.trial)
            self.assertEqual(marker.read_text(), "existing profile")
