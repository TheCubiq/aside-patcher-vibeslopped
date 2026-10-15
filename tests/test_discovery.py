"""Check browser identity before any installation operation."""

from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher.discovery import validate_browser_images


def write_image(path: Path, machine: int = 0x8664, magic: int = 0x20B) -> None:
    """Write only the PE header fields needed by the discovery checks."""
    data = bytearray(90)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 64)
    data[64:68] = b"PE\0\0"
    struct.pack_into("<H", data, 68, machine)
    struct.pack_into("<H", data, 88, magic)
    path.write_bytes(data)


class TestBrowserIdentity(unittest.TestCase):
    """Use synthetic headers and mocked resources, never installed browser files."""

    def setUp(self):
        """Create an independent launcher and DLL for each check."""
        directory = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.item = {
            "version": "1.0.928.2",
            "executable": str(root / "Aside.exe"),
            "dll": str(root / "Aside.dll"),
        }
        for name in ("executable", "dll"):
            write_image(Path(self.item[name]))

    def test_directory_version_cannot_hide_mixed_browser_files(self):
        """Require both actual resource versions to match the discovered version."""
        with patch("aside_patcher.discovery.file_version", return_value="1.0.928.2"):
            validate_browser_images(self.item)
        with patch("aside_patcher.discovery.file_version", side_effect=["1.0.928.2", "1.0.922.1"]):
            with self.assertRaisesRegex(Refusal, "mixed versions"):
                validate_browser_images(self.item)

    def test_other_architectures_and_truncated_images_are_refused(self):
        """Reject unsupported machine types and invalid headers before resources."""
        dll = Path(self.item["dll"])
        with patch("aside_patcher.discovery.file_version", return_value="1.0.928.2"):
            for machine, magic in ((0x14C, 0x10B), (0xAA64, 0x20B)):
                with self.subTest(machine=machine):
                    write_image(dll, machine, magic)
                    with self.assertRaisesRegex(Refusal, "Windows x64"):
                        validate_browser_images(self.item)
            dll.write_bytes(b"MZ")
            with self.assertRaisesRegex(Refusal, "valid Windows executable"):
                validate_browser_images(self.item)
