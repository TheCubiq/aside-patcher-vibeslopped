"""Keep developer validation out of the frozen client."""

from pathlib import Path
import tempfile
import unittest

from scripts.package_alpha import verify_client_modules


class TestClientPackaging(unittest.TestCase):
    def test_runtime_modules_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            (build / "PYZ-00.toc").write_text(
                repr(("engine.pyz", [("aside_patcher.protocol", "aside_patcher/protocol.py", "PYMODULE")])),
                encoding="utf-8",
            )
            verify_client_modules(build)

    def test_developer_modules_are_rejected(self):
        for name, source in (
            ("unittest.main", "unittest/main.py"),
            ("tests.test_protocol", "tests/test_protocol.py"),
            ("pytest", "pytest/__init__.py"),
            ("patch_analysis", "scripts/patch_analysis.py"),
        ):
            with self.subTest(module=name), tempfile.TemporaryDirectory() as directory:
                build = Path(directory)
                (build / "PYZ-00.toc").write_text(repr(("engine.pyz", [(name, source, "PYMODULE")])), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Developer modules must not ship"):
                    verify_client_modules(build)
