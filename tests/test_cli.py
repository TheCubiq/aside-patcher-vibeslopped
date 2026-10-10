"""Check public commands without touching installed files."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from aside_patcher.cli import main


class TestCli(unittest.TestCase):
    """Verify listing and independent patch command dispatch."""

    def test_list_does_not_inspect_installed_files(self):
        """List patch metadata without invoking installation operations."""
        output = io.StringIO()
        with redirect_stdout(output), patch("aside_patcher.cli.run_installation") as install:
            self.assertEqual(main(["list"]), 0)
        install.assert_not_called()
        self.assertIn("chats-toggle:", output.getvalue())

    def test_enable_and_disable_dispatch_selected_ids(self):
        """Forward patch IDs and operation names to the shared installer."""
        for command in ("enable", "disable"):
            with patch("aside_patcher.cli.run_installation", return_value=0) as install:
                self.assertEqual(main([command, "chats-toggle", "ask-aside"]), 0)
                self.assertEqual(install.call_args.args[1:], (command, ["chats-toggle", "ask-aside"]))
