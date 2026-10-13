# Test names describe pinned payload safety and unwind contracts.
# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
import struct
from unittest import TestCase
from unittest.mock import patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher.patches.bookmark_folder_appearance.native import (
    HOOKS,
    build_native_appearance,
    load_payload,
    relocation_value,
)


class TestBookmarkPayload(TestCase):
    def test_unknown_image_is_rejected_before_reading_payload(self):
        with patch("aside_patcher.patches.bookmark_folder_appearance.native.load_payload") as loader:
            with self.assertRaises(Refusal):
                build_native_appearance(b"unsupported")
            loader.assert_not_called()

    def test_frozen_sources_and_all_hook_exports_match(self):
        metadata, blob = load_payload()
        self.assertEqual(len(blob), metadata["code_size"] + metadata["data_size"])
        for name, *_ in HOOKS:
            self.assertIn("Bfa" + name + "Hook", metadata["symbols"])
        self.assertEqual(len(metadata["callbacks"]), len(set(metadata["callbacks"])))
        for begin, end, unwind in metadata["unwind"]:
            self.assertLess(begin, end)
            self.assertLess(end, metadata["code_size"] + 1)
            self.assertLess(unwind, metadata["code_size"])
        self.assertEqual(blob[metadata["code_size"] :], bytes(metadata["data_size"]))

    def test_relative_and_image_relative_relocations(self):
        buffer = bytearray(4)
        relocation_value(4, buffer, 0, 0x1000, 0x2000)
        self.assertEqual(struct.unpack("<i", buffer)[0], 0xFFC)
        relocation_value(3, bytearray(4), 0, 0x1000, 0x2000)
        with self.assertRaises(Refusal):
            relocation_value(1, bytearray(8), 0, 0x1000, 0x2000)
