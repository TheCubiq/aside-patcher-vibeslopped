# Test names describe assertions; fixtures live until unittest addCleanup.
# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
# pylint: disable=too-many-instance-attributes,consider-using-with
import io
import unittest
from contextlib import ExitStack, redirect_stdout

from aside_patcher.common.utils import sha256
from aside_patcher.installation import run_installation
from tests.fixtures import TestInstallationFixture


class TestInstallation(TestInstallationFixture):
    def setUp(self):
        super().setUp()
        self.output = io.StringIO()
        capture = ExitStack()
        capture.enter_context(redirect_stdout(self.output))
        self.addCleanup(capture.close)

    def test_independent_patch_transitions(self):
        transitions = (
            ("enable", ["chats", "ask-aside"], ("ask-aside", "chats")),
            ("enable", ["chats-toggle"], ("ask-aside", "chats-toggle")),
            ("enable", ["chats-toggle"], ("ask-aside", "chats-toggle")),
            ("disable", ["ask-aside"], ("chats-toggle",)),
            ("disable", ["ask-aside"], ("chats-toggle",)),
            ("enable", ["ask-aside"], ("ask-aside", "chats-toggle")),
            ("disable", ["chats-toggle"], ("ask-aside",)),
            ("disable", ["ask-aside"], ()),
            ("enable", ["chats-toggle", "ask-aside"], ("ask-aside", "chats-toggle")),
            ("enable", ["chats"], ("ask-aside", "chats")),
        )
        for command, targets, expected in transitions:
            with self.subTest(command=command, targets=targets, expected=expected):
                run_installation(self.build, command, targets)
                observed = {name: sha256(artifact.path.read_bytes()) for name, artifact in self.files.items()}
                self.assertEqual(observed, self.build.states[expected])

    def test_restore_is_byte_for_byte(self):
        run_installation(self.build, "enable", ["chats-toggle", "ask-aside"])
        run_installation(self.build, "restore")
        for name, f in self.files.items():
            self.assertEqual(f.path.read_bytes(), self.original[name])


if __name__ == "__main__":
    unittest.main()
