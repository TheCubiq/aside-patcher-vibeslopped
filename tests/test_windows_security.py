"""Check the native helper's result channel without requesting elevation."""

import os
from pathlib import Path
import tempfile
import unittest

from aside_patcher.windows_security import helper_result


@unittest.skipUnless(os.name == "nt", "Windows file security API")
class TestHelperResult(unittest.TestCase):
    """Keep every permission change scoped to temporary test files."""

    def test_result_is_readable_and_existing_paths_are_never_overwritten(self):
        """Grant ticket-owner access while retaining exclusive result creation."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "request.json"
            request.write_text("{}", encoding="utf-8")
            result = root / "result.json"
            with helper_result(request, result) as stream:
                stream.write("result")
            self.assertEqual(result.read_text(encoding="utf-8"), "result")

            with self.assertRaises(OSError):
                with helper_result(request, result):
                    self.fail("An existing result file must not be opened")
            self.assertEqual(result.read_text(encoding="utf-8"), "result")

    def test_an_existing_hard_link_cannot_redirect_the_privileged_writer(self):
        """An alternate existing pathname must not change an unrelated file."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "request.json"
            request.write_text("{}", encoding="utf-8")
            victim = root / "keep.txt"
            victim.write_text("unchanged", encoding="utf-8")
            result = root / "result.json"
            os.link(victim, result)
            with self.assertRaises(OSError):
                with helper_result(request, result):
                    self.fail("Existing hard links must be refused")
            self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")
