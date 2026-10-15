"""Crash recovery must restore all verified pre-operation artifacts."""

from unittest.mock import patch
from pathlib import Path

from aside_patcher.common.utils import file_sha256
from aside_patcher.recipes import render
from aside_patcher.recovery import recover
from aside_patcher.transaction import replace_transaction
from tests.fixtures import TestInstallationFixture


class TestRecovery(TestInstallationFixture):
    """Exercise a crash between two replacements, rather than a handled error."""

    def test_interrupted_write_recovers_original_state(self):
        """Preserve recovery files on interruption and restore in reverse order."""
        changes = {
            artifact.path: (self.original[name], render(self.original[name], artifact, ("chats-toggle",)))
            for name, artifact in self.files.items()
        }
        journal = self.root / "recovery.json"
        real_hash = file_sha256

        def interrupt(path: Path) -> str:
            if path == self.dll and journal.exists():
                raise KeyboardInterrupt()
            return real_hash(path)

        with patch("aside_patcher.transaction.file_sha256", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                replace_transaction(changes, journal)
        self.assertTrue(journal.exists())
        self.assertTrue(recover(self.build, journal))
        self.assertFalse(journal.exists())
        for name, artifact in self.files.items():
            self.assertEqual(artifact.path.read_bytes(), self.original[name])
