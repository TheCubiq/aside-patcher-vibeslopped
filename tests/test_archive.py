"""Reject malformed package requests before creating archive directories."""

from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import archive_aside as archive


class TestOfficialArchive(unittest.TestCase):
    """Keep acquisition tests offline and outside the immutable vendor archive."""

    def test_package_version_cannot_escape_archive(self):
        """Reject path traversal before network access or directory creation."""
        with tempfile.TemporaryDirectory() as directory, patch.object(archive, "urlopen") as network:
            root = Path(directory) / "not-created"
            with self.assertRaisesRegex(ValueError, "Invalid browser version"):
                archive.acquire_package(root, "../../escaped", Path("7z.exe"))
            self.assertFalse(root.exists())
            network.assert_not_called()

    def test_package_redirect_requires_https_and_official_host(self):
        """An official hostname over HTTP is not an acceptable final download."""
        for url in ("http://releases.aside.com/browser.crx3", "https://example.com/browser.crx3"):
            response = BytesIO(b"untrusted")
            response.url = url
            with (
                self.subTest(url=url),
                tempfile.TemporaryDirectory() as directory,
                patch.object(archive, "urlopen", return_value=response),
            ):
                with self.assertRaisesRegex(ValueError, "Unexpected package redirect"):
                    archive._download_package(  # pylint: disable=protected-access
                        Path(directory), "https://releases.aside.com/browser.crx3"
                    )
                self.assertFalse((Path(directory) / "browser.crx3").exists())
