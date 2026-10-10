"""Check shared process guards and path utilities independently of filesystem mutations."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.utils import backup_directory, require_app_stopped, resolve_manifest_path


class TestUtils(unittest.TestCase):
    """Check shared safety and path utilities."""

    def test_running_process_refuses(self) -> None:
        """Refuse modification while the process check reports a running browser."""
        with patch("aside_patcher.common.utils.aside_is_running", return_value=True):
            with self.assertRaisesRegex(Refusal, "Aside is running"):
                require_app_stopped()

    def test_resolve_manifest_path_expands_environment_variables(self) -> None:
        """Expand percent-delimited Windows environment variables."""
        with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Custom\AppData", "ProgramFiles": r"D:\CustomPrograms"}):
            resolved_app = resolve_manifest_path(r"%LOCALAPPDATA%\Aside\data.json")
            self.assertEqual(resolved_app, Path(r"C:\Custom\AppData\Aside\data.json"))
            resolved_pf = resolve_manifest_path(r"%ProgramFiles%\Aside\Aside.dll")
            self.assertEqual(resolved_pf, Path(r"D:\CustomPrograms\Aside\Aside.dll"))

    def test_resolve_manifest_path_fallback_when_vars_missing(self) -> None:
        """Provide standard fallbacks when environment variables are omitted."""
        fallback_home = Path(r"C:\Users\Fallback")
        with patch.dict(os.environ, {}, clear=True), patch("aside_patcher.common.utils._user_home", return_value=fallback_home):
            resolved = resolve_manifest_path(r"%LOCALAPPDATA%\Aside\data.json")
            self.assertEqual(resolved, fallback_home / "AppData" / "Local" / "Aside" / "data.json")
            resolved_pf = resolve_manifest_path(r"%ProgramFiles%\Aside\Aside.dll")
            self.assertEqual(resolved_pf, Path(r"C:\Program Files\Aside\Aside.dll"))

    def test_resolve_manifest_path_preserves_plain_path(self) -> None:
        """Preserve paths that do not contain environment variables."""
        plain = Path(r"C:\Fixed\Path\file.txt")
        self.assertEqual(resolve_manifest_path(plain), plain)

    def test_backup_directory_uses_fallback_or_refuses(self) -> None:
        """Use home AppData directory when LOCALAPPDATA is absent, or refuse if missing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_home = Path(temp_dir)
            fake_local = fake_home / "AppData" / "Local"
            fake_local.mkdir(parents=True)
            with patch.dict(os.environ, {}, clear=True), patch("aside_patcher.common.utils.Path.home", return_value=fake_home):
                self.assertEqual(backup_directory(), fake_local / "AsidePatcher" / "backups")

        with tempfile.TemporaryDirectory() as empty_home:
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("aside_patcher.common.utils.Path.home", return_value=Path(empty_home)),
                self.assertRaisesRegex(Refusal, "LOCALAPPDATA is unavailable"),
            ):
                backup_directory()


if __name__ == "__main__":
    unittest.main()
