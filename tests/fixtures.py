# Test names describe assertions; fixtures live until unittest addCleanup.
# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
# pylint: disable=too-many-instance-attributes,consider-using-with
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from aside_patcher.common.models import Artifact, Installation
from aside_patcher.common.utils import sha256
from aside_patcher.recipes import render


def fixture_recipe(offset: int, before: bytes, after: bytes) -> dict:
    return {"sites": [{"offset": offset, "before": before.hex(), "after": after.hex()}]}


class TestInstallationFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dll = self.root / "Aside.dll"
        self.ui = self.root / "appearance.js"
        self.dll.write_bytes(b"DLL chats off ask on")
        self.ui.write_bytes(b"UI chats disabled")
        self.original = {
            "dll": self.dll.read_bytes(),
            "appearance": self.ui.read_bytes(),
        }
        self.files = {
            "dll": Artifact(
                self.dll,
                sha256(self.original["dll"]),
                {
                    "chats": fixture_recipe(4, b"chats off", b"chats hid"),
                    "chats-toggle": fixture_recipe(4, b"chats off", b"chats toggle"),
                    "ask-aside": fixture_recipe(14, b"ask on", b"ask no"),
                },
            ),
            "appearance": Artifact(
                self.ui,
                sha256(self.original["appearance"]),
                {
                    "chats-toggle": fixture_recipe(9, b"disabled", b"enabled"),
                },
            ),
        }
        combinations = [
            (),
            ("chats",),
            ("ask-aside",),
            ("chats-toggle",),
            ("ask-aside", "chats"),
            ("ask-aside", "chats-toggle"),
        ]
        states = {
            state: {name: sha256(render(self.original[name], file, state)) for name, file in self.files.items()}
            for state in combinations
        }
        self.build = Installation(
            "test",
            self.files,
            states,
            (("chats", "chats-toggle"),),
        )
        self.backups = self.root / "backups"
        backup_path = patch("aside_patcher.recipes.backup_directory", return_value=self.backups)
        backup_path.start()
        self.addCleanup(backup_path.stop)
        stopped = patch("aside_patcher.common.utils.aside_is_running", return_value=False)
        stopped.start()
        self.addCleanup(stopped.stop)
        self.addCleanup(self.make_backups_writable)

    def make_backups_writable(self):
        if self.backups.exists():
            for p in self.backups.iterdir():
                p.chmod(0o644)


def transaction_changes(test: TestInstallationFixture) -> dict[Path, tuple[bytes, bytes]]:
    return {
        test.dll: (test.original["dll"], b"new DLL"),
        test.ui: (test.original["appearance"], b"new UI"),
    }


def assert_transaction_unchanged(test: TestInstallationFixture) -> None:
    for name, artifact in test.files.items():
        test.assertEqual(artifact.path.read_bytes(), test.original[name])
    test.assertFalse(list(test.root.glob(".*")))
