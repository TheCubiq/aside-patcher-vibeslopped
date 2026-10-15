"""Verify automation selectors and protocol failures cannot become false passes."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.native_ui import Connection, NativeUI, ProtocolError, StaleNodeError
from scripts.run_native_acceptance import run_step, set_theme, validate_suite
from scripts import run_native_acceptance as acceptance


class FakeSocket:
    """Supply deterministic protocol replies without a desktop or WebSocket."""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []

    def send(self, message):
        """Retain exactly what the client sent."""
        self.sent.append(json.loads(message))

    def recv(self, timeout):
        """Return the next response inside the requested timeout."""
        assert timeout > 0
        return json.dumps(next(self.responses))


class FakeNative:
    """Expose same-class labels, hidden controls and real metadata structure."""

    def __init__(self):
        self.timeout = 1
        self.drawn = {1: "true", 2: "true", 3: "false"}
        self.calls = []

    def call(self, method, **parameters):
        """Implement the two read endpoints needed for semantic selection."""
        self.calls.append((method, parameters))
        if method == "DOM.getDocument":
            return {"root": {"children": [{"nodeId": index, "attributes": ["class", "Button"]} for index in self.drawn]}}
        if method == "CSS.getMatchedStylesForNode":
            index = parameters["nodeId"]
            values = {"IsDrawn": self.drawn[index], "Text": "Duplicate" if index < 3 else "Hidden", "Width": "40", "Height": "24"}
            return {
                "matchedCSSRules": [
                    {"rule": {"style": {"cssProperties": [{"name": key, "value": value} for key, value in values.items()]}}}
                ]
            }
        return {}


class TestNativeUI(unittest.TestCase):
    """Require exact matches and observable assertions, never input success alone."""

    def test_duplicate_names_refuse_instead_of_clicking_first(self):
        """Ambiguous native controls cannot receive accidental input."""
        backend = FakeNative()
        with self.assertRaisesRegex(ProtocolError, "found 2"):
            NativeUI(backend).click("Button", "Duplicate")
        self.assertFalse(any(method == "DOM.dispatchMouseEvent" for method, _ in backend.calls))

    def test_hidden_nodes_are_not_interaction_targets(self):
        """A present but hidden control does not satisfy visibility assertions."""
        self.assertEqual(NativeUI(FakeNative()).matches("Button", "Hidden"), [])

    def test_hover_sends_enter_before_move(self):
        """The native submenu callback needs an enter event as well as movement."""
        backend = FakeNative()
        backend.drawn[2] = "false"
        NativeUI(backend).hover("Button", "Duplicate")
        events = [params["event"] for method, params in backend.calls if method == "DOM.dispatchMouseEvent"]
        self.assertEqual([event["type"] for event in events], ["mouseEntered", "mouseMoved"])
        self.assertTrue(all((event["x"], event["y"]) == (20, 12) for event in events))

    def test_unknown_actions_fail_closed(self):
        """Misspelled scenario actions must not silently count as coverage."""
        with self.assertRaisesRegex(ValueError, "Unknown native scenario action"):
            run_step(NativeUI(FakeNative()), {"action": "pretend-pass"})

    def test_protocol_events_do_not_replace_request_response(self):
        """Unsolicited events and other responses cannot satisfy this request."""
        client = Connection.__new__(Connection)
        client.sequence, client.timeout, client.transcript = 0, 1, []
        client.socket = FakeSocket([{"method": "DOM.childNodeInserted"}, {"id": 8, "result": {}}, {"id": 1, "result": {"ok": True}}])
        self.assertEqual(client.call("DOM.getDocument"), {"ok": True})
        self.assertEqual(client.socket.sent[0]["id"], 1)

    def test_disabled_native_input_is_failure(self):
        """The default Chromium input gate requires an explicit test feature flag."""
        client = Connection.__new__(Connection)
        client.sequence, client.timeout, client.transcript = 0, 1, []
        client.socket = FakeSocket([{"id": 1, "error": {"message": "Dispatch mouse events is not enabled."}}])
        with self.assertRaisesRegex(ProtocolError, "not enabled"):
            client.call("DOM.dispatchMouseEvent")

    def test_self_destroying_button_does_not_receive_a_stale_release(self):
        """An icon can close its own popup on press; postconditions remain required."""
        ui = NativeUI(FakeNative())
        ui.mouse = Mock(side_effect=[None, None, None, StaleNodeError("Node with id=4 not found")])
        ui.click_node({"nodeId": 4})
        self.assertEqual(ui.mouse.call_count, 4)

    def test_other_release_errors_are_not_swallowed(self):
        """Transport failures must not be mistaken for a self-closing widget."""
        ui = NativeUI(FakeNative())
        ui.mouse = Mock(side_effect=[None, None, None, ProtocolError("Input disabled")])
        with self.assertRaisesRegex(ProtocolError, "Input disabled"):
            ui.click_node({"nodeId": 4})

    def test_stale_read_snapshot_is_reacquired_whole(self):
        """Navigation can rebuild a row between the tree and metadata requests."""
        ui = NativeUI(FakeNative())
        with patch.object(ui, "_read_matches", side_effect=[StaleNodeError("Node with id=4 not found"), [{"nodeId": 5}]]) as read:
            self.assertEqual(ui.matches("Button"), [{"nodeId": 5}])
            self.assertEqual(read.call_count, 2)

    def test_stale_click_target_is_reacquired_before_press(self):
        """A rejected stale lookup can retry without delivering duplicate clicks."""
        ui = NativeUI(FakeNative())
        ui.find = Mock(side_effect=[StaleNodeError("Node with id=4 not found"), {"nodeId": 5}])
        ui.click_node = Mock()
        ui.click("Button")
        ui.click_node.assert_called_once_with({"nodeId": 5}, "left")

    def test_invalid_theme_does_not_open_settings(self):
        """Unknown enum values must refuse before touching the synthetic browser."""
        browser = Mock()
        with self.assertRaisesRegex(ValueError, "enum values"):
            set_theme(browser, 9337, 99)
        browser.call.assert_not_called()

    def test_empty_or_unsafe_suites_refuse(self):
        """An empty test list or evidence path traversal cannot count as acceptance."""
        suite = {"schema_version": 1, "browser_version": "1.0.928.2", "cases": []}
        with self.assertRaisesRegex(ValueError, "nonempty"):
            validate_suite(suite, "1.0.928.2")
        suite["cases"] = [{"id": "../escape", "steps": [{}]}]
        with self.assertRaisesRegex(ValueError, "safe evidence filename"):
            validate_suite(suite, "1.0.928.2")

    def test_failure_after_cases_passed_still_exits_nonzero(self):
        """A restart or restored-UI failure cannot inherit an earlier passing flag."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            arguments = ["runner", "--trial", directory, "--suite", "unused.json", "--report", str(output)]

            def failed_restart(_args, report):
                report.update(scenario_passed=True, restored=True)
                raise ValueError("Restart UI mismatch")

            with patch("sys.argv", arguments), patch.object(acceptance, "exercise", side_effect=failed_restart):
                self.assertEqual(acceptance.main(), 2)
            report = json.loads(output.read_text())
            self.assertFalse(report["scenario_passed"])
            self.assertEqual(report["status"], "finished")

    def test_deleted_identity_cannot_survive_in_profile_storage(self):
        """Persisted decorations must stay tied to folder identity after removal."""
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            (profile / "Default").mkdir()
            guid = "a542598d-b063-5640-a1df-874fe3e52d3e"
            preferences = {"aside": {"bookmark_folder_appearance": f"v1\n{guid}\t4\trocket\n"}}
            (profile / "Default" / "Preferences").write_text(json.dumps(preferences))
            suite = {"absent_appearance": [guid]}
            with self.assertRaisesRegex(ValueError, "Deleted folder decoration"):
                acceptance.verify_profile(profile, suite)
