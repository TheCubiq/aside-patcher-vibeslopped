# Test names describe assertions; builders model dependent native image transforms.
# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
from pathlib import Path
from unittest import TestCase

from aside_patcher.common.models import Artifact
from aside_patcher.common.utils import sha256
from aside_patcher.recipes import render


class TestBuilderOrder(TestCase):
    def test_dependent_builders_run_before_static_sites_in_either_selection_order(self):
        calls = []

        def first(data: bytes) -> bytes:
            self.assertEqual(data, b"original")
            calls.append("first")
            return data + b" toggle"

        def second(data: bytes) -> bytes:
            self.assertEqual(data, b"original toggle")
            calls.append("second")
            return data + b" folders"

        artifact = Artifact(
            Path("Aside.dll"),
            sha256(b"original"),
            {
                "folders": {"builder": second, "builder_order": 20},
                "toggle": {"builder": first},
                "static": {"sites": [{"offset": 0, "before": b"original".hex(), "after": b"modified".hex()}]},
            },
        )
        for targets in (("folders", "static", "toggle"), ("toggle", "folders", "static")):
            calls.clear()
            self.assertEqual(render(b"original", artifact, targets), b"modified toggle folders")
            self.assertEqual(calls, ["first", "second"])
