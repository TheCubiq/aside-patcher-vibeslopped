"""Check native discovery ambiguity and retained object-layout operands."""

import re
import struct
import unittest

from scripts.native_discovery import NativeDiscovery
from scripts.patch_analysis import masked_bytes
from scripts.native_resources import code_equal, read, vector_identity
from aside_patcher.pe import parse_layout
from tests.test_patch_analysis import image


def native_image(code: bytes, position: int = 512, duplicate: bool = False) -> bytes:
    """Add x64 unwind boundaries to a synthetic executable PE fixture."""
    data = bytearray(image(code, position))
    ranges = [(position + 0xE00, position + 0xE00 + len(code), 0)]
    if duplicate:
        data[position + 64 : position + 64 + len(code)] = code
        ranges.append((position + 0xE40, position + 0xE40 + len(code), 0))
    struct.pack_into("<II", data, 88 + 136, 0x1380, len(ranges) * 12)
    for index, row in enumerate(ranges):
        struct.pack_into("<III", data, 1408 + index * 12, *row)
    return bytes(data)


class TestNativeDiscovery(unittest.TestCase):
    """A candidate map never grants installation approval."""

    def test_shifted_complete_function_is_found(self):
        """Move a function while preserving its exact field access."""
        code = bytes.fromhex("488b81400400004883c001c3")
        report = NativeDiscovery(native_image(code), native_image(code, 544)).discover([0x1000])
        self.assertEqual(report["dependencies"]["0x1000"]["target_rva"], 0x1020)
        self.assertFalse(report["installable"])

    def test_changed_field_offset_is_not_masked(self):
        """An object-layout change requires manual analysis."""
        before = bytes.fromhex("488b8140040000c3")
        after = bytes.fromhex("488b8148040000c3")
        report = NativeDiscovery(native_image(before), native_image(after)).discover([0x1000])
        self.assertEqual(report["dependencies"]["0x1000"]["status"], "unresolved")

    def test_duplicate_complete_functions_are_rejected(self):
        """Matching instructions at multiple unwind ranges remain ambiguous."""
        code = bytes.fromhex("488b8140040000c3")
        report = NativeDiscovery(native_image(code), native_image(code, duplicate=True)).discover([0x1000])
        self.assertEqual(len(report["dependencies"]["0x1000"]["matches"]), 2)

    def test_prefixed_simd_rip_displacement_masks_all_four_bytes(self):
        """Do not trust Capstone's incorrect disp_size for this instruction."""
        before = bytes.fromhex("660f6f15edc3f10b")
        after = bytes.fromhex("660f6f15dd52f20b")
        pattern, mask = masked_bytes(before)
        self.assertEqual(mask, {4, 5, 6, 7})
        self.assertIsNotNone(re.fullmatch(pattern, after, re.DOTALL))

    def test_vector_identity_checks_path_contents(self):
        """Vector names alone cannot establish resource identity."""
        data = bytearray(native_image(b"\xc3"))
        base = 0x180000000
        struct.pack_into("<II", data, 768, 28, 0x41800000)
        struct.pack_into("<QQ", data, 832, base + 0x1100, 2)
        data[864:871] = b"Folder\0"
        struct.pack_into("<QQQ", data, 896, base + 0x1140, 1, base + 0x1160)
        layout = parse_layout(data)
        before = vector_identity(data, layout, 0x1180)
        data[768] = 29
        self.assertNotEqual(before, vector_identity(data, layout, 0x1180))

    def test_resource_reads_are_bounded(self):
        """Malformed counts must not authorize arbitrary resource spans."""
        data = native_image(b"\xc3")
        with self.assertRaisesRegex(ValueError, "Unreasonable"):
            read(data, parse_layout(data), 0x1000, 2**32)

    def test_time_budget_returns_unresolved_evidence(self):
        """An expired analysis budget cannot turn a dependency into support."""
        discovery = NativeDiscovery(native_image(b"\xc3"), native_image(b"\xc3"))
        discovery.deadline = 0
        report = discovery.discover([0x1000])
        self.assertFalse(report["installable"])
        self.assertEqual(report["remaining"], 1)
        self.assertIn("time budget", report["dependencies"]["0x1000"]["reason"])

    def test_tail_thunks_require_matching_destination_methods(self):
        """A matching adjustment and JMP cannot hide changed destination fields."""
        baseline = bytearray(native_image(bytes.fromhex("488b4140c3")))
        baseline[576:585] = bytes.fromhex("4883c108e9") + struct.pack("<i", 0x1000 - 0x1049)
        target = bytearray(native_image(bytes.fromhex("488b4140c3"), 544))
        target[608:617] = bytes.fromhex("4883c108e9") + struct.pack("<i", 0x1020 - 0x1069)
        self.assertTrue(code_equal(NativeDiscovery(bytes(baseline), bytes(target)), 0x1040, 0x1060))
        target[547] = 0x48
        self.assertFalse(code_equal(NativeDiscovery(bytes(baseline), bytes(target)), 0x1040, 0x1060))
