"""Check refusal boundaries of the native recipe builder."""

import unittest

from aside_patcher.common.exceptions import Refusal
from aside_patcher.patches.chats_toggle.native import build_native_toggle


class TestRecipes(unittest.TestCase):
    """Check native recipe inputs."""

    def test_native_builder_refuses_unknown_original(self):
        """Refuse native hook generation for an unverified original."""
        with self.assertRaises(Refusal):
            build_native_toggle(b"unknown DLL")
