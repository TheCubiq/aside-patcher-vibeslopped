# Test names describe assertions; fixtures live until unittest addCleanup.
# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
# pylint: disable=too-many-instance-attributes,consider-using-with
from unittest.mock import patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.utils import preserve_original
from tests.fixtures import TestInstallationFixture


class TestBackups(TestInstallationFixture):
    def test_original_backup_cannot_be_overwritten(self):
        f = self.files["dll"]
        backup = preserve_original(self.original["dll"], f, self.backups)
        self.assertEqual(preserve_original(self.original["dll"], f, self.backups), backup)
        backup.chmod(0o644)
        backup.write_bytes(b"corrupt")
        with self.assertRaises(Refusal):
            preserve_original(self.original["dll"], f, self.backups)
        self.assertEqual(backup.read_bytes(), b"corrupt")

    def test_failed_backup_write_does_not_leave_corrupt_backup(self):
        with (
            patch(
                "aside_patcher.common.utils.os.fsync",
                side_effect=OSError("flush failed"),
            ),
            self.assertRaises(Refusal),
        ):
            preserve_original(self.original["dll"], self.files["dll"], self.backups)
        self.assertEqual(list(self.backups.iterdir()), [])
