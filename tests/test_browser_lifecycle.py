"""Keep launch acceptance separate from launcher exit codes and installed hashes."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher import protocol
from tests.fixtures import TestInstallationFixture


class TestBrowserRestart(unittest.TestCase):
    """Exercise startup outcomes without launching the user's browser."""

    def test_clean_launcher_exit_without_browser_is_not_success(self):
        """An exit-zero launcher alone does not establish a running browser."""
        with (
            patch.object(protocol.subprocess, "Popen", return_value=Mock(poll=Mock(return_value=0))),
            patch.object(protocol.time, "sleep"),
            patch.object(protocol, "browser_processes", return_value=[]),
        ):
            with self.assertRaisesRegex(Refusal, "browser launch failed"):
                protocol._restart_browser("trial/Aside.exe", [])  # pylint: disable=protected-access

    def test_restart_keeps_profile_arguments_and_requires_live_image(self):
        """An exact live image can validate a successful launcher handoff."""
        arguments = ["--user-data-dir=C:\\Synthetic Profile", "--profile-directory=Default"]
        with (
            patch.object(protocol.subprocess, "Popen", return_value=Mock(poll=Mock(return_value=0))) as launch,
            patch.object(protocol.time, "sleep"),
            patch.object(protocol, "browser_processes", return_value=[123]) as processes,
        ):
            protocol._restart_browser("trial/Aside.exe", arguments)  # pylint: disable=protected-access
        launch.assert_called_once_with(["trial/Aside.exe", *arguments])
        processes.assert_called_once_with(Path("trial/Aside.exe"))


class TestCanceledInstallation(TestInstallationFixture):
    """Keep original bytes and restore the user's launch context on cancellation."""

    def test_uac_cancellation_restarts_original_browser_without_mutation(self):
        """A declined helper retains its error code and the selected profile."""
        preview = protocol.preview_build(self.build, ["chats"])
        item = {"executable": str(self.root / "Aside.exe"), "dll": str(self.dll)}
        request = {"operation": "apply", "targets": ["chats"], "preview_token": preview["token"]}
        with (
            patch.object(protocol, "_resolve", return_value=(item, self.root, self.build, False)),
            patch.object(protocol, "launch_arguments", return_value=["--user-data-dir=synthetic"]),
            patch.object(protocol, "close_browser", return_value=True),
            patch.object(protocol, "_run_mutation", side_effect=Refusal("Canceled", code="elevation_declined")),
            patch.object(protocol.subprocess, "Popen") as launch,
        ):
            with self.assertRaises(Refusal) as raised:
                protocol._apply_request(request, "apply")  # pylint: disable=protected-access
        self.assertEqual(raised.exception.code, "elevation_declined")
        self.assertEqual(launch.call_args.args[0], [item["executable"], "--user-data-dir=synthetic"])
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])
        self.assertEqual(self.ui.read_bytes(), self.original["appearance"])


if __name__ == "__main__":
    unittest.main()
