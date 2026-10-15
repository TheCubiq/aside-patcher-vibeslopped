"""Keep failed combination tests confined to their fixture and restore afterward."""

import json
from unittest.mock import patch

from scripts import test_desktop_engine as trial
from tests.fixtures import TestInstallationFixture


class TestTrialEngine(TestInstallationFixture):
    """Model a failed case after a file has changed, without browser processes."""

    def test_failed_case_still_restores_originals(self):
        """A failed apply may not bypass the fixture's final restoration."""
        context = {"installation": str(self.dll), "data_root": str(self.root / "profile")}

        def dispatch(request: dict) -> dict:
            """Simulate a failure after replacement and a successful restoration."""
            operation = request["operation"]
            if operation == "inspect":
                return {"current": [], "restore_supported": True, "combinations": [("chats",)]}
            if operation == "preview":
                self.assertIsInstance(request["targets"], list)
                return {"token": "synthetic", "expected": self.build.states[tuple(request["targets"])]}
            if operation == "apply":
                self.dll.write_bytes(b"candidate installed before failure")
                raise ValueError("simulated case failure")
            if operation == "restore":
                self.dll.write_bytes(self.original["dll"])
                return {"current": [], "hashes": self.build.states[()]}
            raise AssertionError(f"Unexpected operation {operation}")

        with patch.object(trial, "dispatch", side_effect=dispatch) as engine:
            with self.assertRaisesRegex(ValueError, "simulated case failure"):
                trial.exercise(context)
        self.assertEqual(engine.call_args.args[0]["operation"], "restore")
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])

    def test_settings_profile_cannot_escape_trial(self):
        """A local DLL does not authorize mutations to a different profile."""
        context = {"installation": str(self.dll), "data_root": str(self.root.parent / "other-profile")}
        (self.root / "request-context.json").write_text(json.dumps(context))
        with self.assertRaisesRegex(ValueError, "data_root escaped"):
            trial.trial_context(self.root)
