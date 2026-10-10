"""Check upgrades from explicitly verified earlier patch revisions."""

from dataclasses import replace

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.utils import parse_previous_states, preserve_original, sha256
from aside_patcher.installation import run_installation
from tests.fixtures import TestInstallationFixture


class TestRevisions(TestInstallationFixture):
    """Allow revision upgrades without accepting unknown or mixed files."""

    def prior_installation(self):
        """Create an accepted older DLL with the current settings asset.

        Returns
        -------
        Installation
            Fixture with one explicitly supported prior input state.
        """
        state = ("ask-aside", "chats-toggle")
        for name, artifact in self.files.items():
            preserve_original(self.original[name], artifact, self.backups)
        self.dll.write_bytes(b"older verified DLL")
        self.ui.write_bytes(b"UI chats enabled")
        hashes = {name: sha256(artifact.path.read_bytes()) for name, artifact in self.files.items()}
        return replace(self.build, previous_states=((state, hashes),))

    def test_enable_upgrades_same_active_targets(self):
        """Rebuild an old revision even when the requested target IDs match."""
        build = self.prior_installation()
        run_installation(build, "enable", ["chats-toggle"])
        self.assertEqual(self.dll.read_bytes(), b"DLL chats toggle ask no")
        self.assertEqual(self.ui.read_bytes(), b"UI chats enabled")
        self.assertEqual((self.backups / f'{self.files["dll"].original_sha256}.dll').read_bytes(), self.original["dll"])

    def test_restore_previous_revision(self):
        """Restore accepted prior revisions to exact vendor originals."""
        run_installation(self.prior_installation(), "restore")
        for name, artifact in self.files.items():
            self.assertEqual(artifact.path.read_bytes(), self.original[name])

    def test_mixed_previous_files_refuse(self):
        """Refuse a known old DLL paired with an incompatible settings asset."""
        build = self.prior_installation()
        self.ui.write_bytes(self.original["appearance"])
        with self.assertRaises(Refusal):
            run_installation(build, "enable", ["chats-toggle"])
        self.assertEqual(self.dll.read_bytes(), b"older verified DLL")

    def test_ambiguous_or_incomplete_revision_metadata_refuses(self):
        """Require distinct prior hashes, complete files, and supported targets."""
        states = self.build.states
        invalid = (
            {"targets": [], "sha256": states[()]},
            {"targets": ["unknown"], "sha256": states[()]},
            {"targets": ["chats-toggle"], "sha256": {"dll": "older"}},
        )
        for entry in invalid:
            with self.assertRaises(Refusal):
                parse_previous_states([entry], states)
