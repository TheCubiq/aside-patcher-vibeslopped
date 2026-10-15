"""Keep per-selection runtime scenarios honest and independent."""

import json
from pathlib import Path
import unittest

from scripts.native_matrix import suite_for_targets


class TestNativeMatrix(unittest.TestCase):
    """Use the reviewed scenario template without starting a browser."""

    def setUp(self):
        """Read the same adapter consumed by the interactive runner."""
        self.template = json.loads((Path(__file__).parent / "fixtures" / "native-1.0.928.2.json").read_text())

    def test_disabled_bookmarks_do_not_run_customization_cases(self):
        """Unpatched bookmark rendering must not advertise picker/reset coverage."""
        suite = suite_for_targets(self.template, [])
        self.assertEqual(suite["targets"], [])
        self.assertNotIn("choose-color", {case["id"] for case in suite["cases"]})
        self.assertEqual(suite["expected_appearance"], {})
        steps = suite["cases"][0]["steps"]
        ask = next(step for step in steps if step.get("selector", {}).get("text") == "Ask Aside")
        self.assertEqual(ask["expected"], 1)

    def test_chats_and_ask_aside_assertions_follow_the_selection(self):
        """Toolbar patches have independent assertions in a combined selection."""
        suite = suite_for_targets(self.template, ["chats", "ask-aside", "bookmark-folder-appearance"])
        steps = suite["cases"][0]["steps"]
        for title in ("Chats", "Ask Aside"):
            check = next(step for step in steps if step.get("selector", {}).get("text") == title)
            self.assertEqual(check["expected"], 0)
        self.assertIn("choose-icon", {case["id"] for case in suite["cases"]})

    def test_scoping_does_not_mutate_the_template(self):
        """One selection cannot change the next selection's expected controls."""
        before = json.dumps(self.template, sort_keys=True)
        suite_for_targets(self.template, [])
        suite_for_targets(self.template, ["chats"])
        self.assertEqual(json.dumps(self.template, sort_keys=True), before)

    def test_missing_settings_test_adapter_refuses(self):
        """Do not silently label an unimplemented settings scenario as passing."""
        with self.assertRaisesRegex(ValueError, "settings patch"):
            suite_for_targets(self.template, ["chats-toggle"])
