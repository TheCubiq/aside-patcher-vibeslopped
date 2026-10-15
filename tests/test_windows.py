"""Verify shutdown races cannot bypass live-process safety checks."""

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher import windows


class TestProcessExitRace(unittest.TestCase):
    """Exercise native API outcomes without touching real browser processes."""

    def test_exited_process_is_skipped_but_unreadable_live_process_is_refused(self):
        """An exited snapshot entry is harmless; a live unknown image is not."""
        for exit_code in (0, 259):
            kernel = Mock()
            kernel.QueryFullProcessImageNameW.return_value = False

            def read_exit_code(_handle: int, output: object, code: int = exit_code) -> bool:
                """Populate the native output argument with this test's exit code."""
                output._obj.value = code  # pylint: disable=protected-access
                return True

            kernel.GetExitCodeProcess.side_effect = read_exit_code
            with self.subTest(exit_code=exit_code):
                if exit_code == 259:
                    with self.assertRaises(Refusal):
                        windows._matches_image(kernel, 42, Path("Aside.exe"))  # pylint: disable=protected-access
                else:
                    self.assertFalse(windows._matches_image(kernel, 42, Path("Aside.exe")))  # pylint: disable=protected-access
                kernel.CloseHandle.assert_called_once_with(42)


class TestElevationErrors(unittest.TestCase):
    """Distinguish user cancellation from a broken helper launch."""

    def test_native_error_codes_are_preserved(self):
        """Missing executables must not be reported as a declined UAC prompt."""
        for error, expected in ((1223, "elevation_declined"), (5, "elevation_declined"), (2, "elevation_failed")):
            shell = Mock()
            shell.ShellExecuteExW.return_value = False
            with (
                self.subTest(error=error),
                patch.object(windows.ctypes.windll.shell32, "ShellExecuteExW", return_value=False),
                patch.object(windows.ctypes, "WinDLL", return_value=shell),
                patch.object(windows.ctypes, "get_last_error", return_value=error),
            ):
                with self.assertRaises(Refusal) as raised:
                    windows.elevate(["-m", "aside_patcher.protocol"], Path.cwd())
                self.assertEqual(raised.exception.code, expected)


class TestLaunchContext(unittest.TestCase):
    """Preserve quoted Windows paths and refuse ambiguous profile sessions."""

    def test_custom_user_data_root_is_not_replaced_with_default(self):
        """Support both Chromium flag forms and refuse unresolved relative paths."""
        default = Path("C:/Default Profile")
        custom = Path("D:/Custom Profile")
        for arguments in ([f"--user-data-dir={custom}"], ["--user-data-dir", str(custom)]):
            self.assertEqual(windows.profile_data_root(arguments, default), custom.resolve())
        self.assertEqual(windows.profile_data_root([], default), default.resolve())
        for invalid in (
            ["--user-data-dir"],
            ["--user-data-dir="],
            ["--user-data-dir=relative"],
            [f"--user-data-dir={custom}", f"--user-data-dir={default}"],
        ):
            with self.subTest(arguments=invalid), self.assertRaises(Refusal):
                windows.profile_data_root(invalid, default)

    def test_quoted_profile_arguments_survive_renderer_filtering(self):
        """Renderer invocations may not displace the selected main invocation."""
        commands = [
            '"C:\\Aside Trial\\Aside.exe" --type=renderer --lang=en-US',
            '"C:\\Aside Trial\\Aside.exe" "--user-data-dir=D:\\Profile With Spaces" --profile-directory=Default',
        ]
        self.assertEqual(
            windows._main_launch_arguments(commands),  # pylint: disable=protected-access
            ["--user-data-dir=D:\\Profile With Spaces", "--profile-directory=Default"],
        )

    def test_multiple_mains_and_missing_context_are_refused(self):
        """Never silently forget another running user-data session during restart."""
        commands = ['Aside.exe "--user-data-dir=C:\\One"', 'Aside.exe "--user-data-dir=C:\\Two"']
        with self.assertRaisesRegex(Refusal, "Multiple Aside profile sessions"):
            windows._main_launch_arguments(commands)  # pylint: disable=protected-access
        for incomplete in (None, [], [None], ["Aside.exe --type=renderer"]):
            with self.subTest(incomplete=incomplete), self.assertRaises(Refusal):
                windows._main_launch_arguments(incomplete)  # pylint: disable=protected-access
