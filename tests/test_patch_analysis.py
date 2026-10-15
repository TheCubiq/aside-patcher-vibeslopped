"""Exercise cross-version instruction resolution without vendor binaries."""

from dataclasses import replace
import struct
import unittest

from aside_patcher.common.models import PatchDefinition
from scripts.patch_analysis import (
    analyze_patch,
    eligibility,
    masked_bytes,
    relocate_replacement,
    render_selection,
    selections,
    smoke_selections,
)


def image(code: bytes, position: int = 512) -> bytes:
    """Place a small x64 function into one synthetic executable PE section."""
    data = bytearray(1536)
    struct.pack_into("<I", data, 60, 64)
    struct.pack_into("<H", data, 70, 1)
    struct.pack_into("<H", data, 84, 240)
    struct.pack_into("<Q", data, 112, 0x180000000)
    section = 64 + 24 + 240
    struct.pack_into("<IIII", data, section + 8, 1024, 0x1000, 1024, 512)
    struct.pack_into("<I", data, section + 36, 0x60000020)
    data[position : position + len(code)] = code
    return bytes(data)


class TestPatchAnalysis(unittest.TestCase):
    """Require unique contexts, unchanged field accesses and correct branch relocation."""

    def setUp(self):
        self.before = bytes.fromhex("89da")
        self.code = self.before + bytes.fromhex("488b41404883c008") + b"\x90" * 90 + b"\xc3"
        self.definition = PatchDefinition(
            "sample",
            "Example patch",
            ("1.0.928.2",),
            (),
            {
                "dll": {"sites": [{"offset": 512, "before": self.before.hex(), "after": "31d2"}]},
            },
            compatibility={"analyzer": "instruction-sites"},
        )

    def test_candidate_moves_with_unique_function_context(self):
        """Find relocated functions without hardcoded new file offsets."""
        result = analyze_patch(self.definition, "1.0.922.1", image(self.code), image(self.code, 768))
        self.assertTrue(result["analysis_passed"], result)
        self.assertEqual(result["sites"][0]["offset"], 768)

    def test_ambiguous_and_changed_layouts_are_refused(self):
        """Neither duplicate functions nor changed object offsets may become candidates."""
        duplicate = bytearray(image(self.code))
        duplicate[768 : 768 + len(self.code)] = self.code
        result = analyze_patch(self.definition, "1.0.922.1", image(self.code), bytes(duplicate))
        self.assertFalse(result["analysis_passed"])
        self.assertIn("found 2", result["reason"])
        changed = self.code.replace(bytes.fromhex("488b4140"), bytes.fromhex("488b4148"))
        self.assertFalse(analyze_patch(self.definition, "1.0.922.1", image(self.code), image(changed))["analysis_passed"])

    def test_ranges_are_numeric_and_do_not_grant_support(self):
        """A version range is an eligibility condition, not verification evidence."""
        definition = replace(self.definition, compatibility={"minimum_version": "1.0.99.1", "maximum_version": "1.0.928.1"})
        self.assertIsNone(eligibility(definition, "1.0.100.1"))
        self.assertIn("maximum", eligibility(definition, "1.0.928.2"))
        self.assertFalse(analyze_patch(definition, "1.0.100.1", image(self.code), image(self.code))["analysis_passed"])

    def test_rewritten_branch_preserves_actual_destination(self):
        """Changing a conditional branch to a jump must retain the new build's target."""
        old = bytes.fromhex("4084ff0f84df000000")
        after = bytes.fromhex("4084ffe9e000000090")
        actual = bytes.fromhex("4084ff0f84ef000000")
        result = relocate_replacement(actual, old, after, 0x1000)
        self.assertEqual(result, bytes.fromhex("4084ffe9f000000090"))

    def test_instruction_mask_keeps_field_offsets(self):
        """Only address-dependent references are masked; object layouts remain exact."""
        self.assertEqual(masked_bytes(bytes.fromhex("488b4140"))[1], set())
        self.assertEqual(masked_bytes(bytes.fromhex("e801000000"))[1], {1, 2, 3, 4})

    def test_unverified_capabilities_cannot_be_ignored(self):
        """A matching instruction site does not waive a patch's other prerequisites."""
        definition = replace(self.definition, compatibility={"analyzer": "instruction-sites", "requires": ["new-native-api"]})
        result = analyze_patch(definition, "1.0.922.1", image(self.code), image(self.code))
        self.assertFalse(result["analysis_passed"])
        self.assertIn("new-native-api", result["reason"])

    def test_conflicts_and_overlaps_do_not_silently_combine(self):
        """Selection enumeration respects conflicts and the renderer rejects overlaps."""
        patches = [
            {"id": "a", "analysis_passed": True, "conflicts": ["b"], "sites": [{"offset": 0, "before": "61", "after": "62"}]},
            {"id": "b", "analysis_passed": True, "conflicts": ["a"], "sites": [{"offset": 0, "before": "61", "after": "63"}]},
        ]
        self.assertNotIn(("a", "b"), selections(patches))
        with self.assertRaisesRegex(ValueError, "Overlapping"):
            render_selection(b"a", patches, ("a", "b"))

    def test_smoke_sets_cover_conflicting_maximal_selections(self):
        """Cover valid interactions without requiring impossible all-patch selections."""
        patches = [{"id": name, "status": "supported", "files": ["dll"]} for name in ("a", "b", "c")]
        states = [{"targets": targets} for targets in ([], ["a"], ["b"], ["c"], ["a", "b"], ["a", "c"])]
        self.assertEqual(smoke_selections(states, patches), [["a"], ["b"], ["c"], ["a", "b"], ["a", "c"]])
