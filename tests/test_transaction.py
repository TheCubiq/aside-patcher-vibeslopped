# Test names describe assertions; fixtures live until unittest addCleanup.
# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
# pylint: disable=too-many-instance-attributes,consider-using-with
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher.transaction import replace_transaction, stage_replacement
from tests.fixtures import TestInstallationFixture, assert_transaction_unchanged, transaction_changes


class TestTransaction(TestInstallationFixture):
    @unittest.skipUnless(os.name == "nt", "Windows read-only file attribute")
    def test_read_only_artifact_refuses_before_any_staging(self):
        self.ui.chmod(0o444)
        self.addCleanup(self.ui.chmod, 0o600)
        with patch("aside_patcher.transaction.stage_replacement") as stage:
            with self.assertRaisesRegex(Refusal, "read-only"):
                replace_transaction(transaction_changes(self))
            stage.assert_not_called()
        assert_transaction_unchanged(self)

    def test_all_replacements_staged_before_permission_failure(self):
        changes = transaction_changes(self)

        def fail_ui(path: Path, data: bytes, label: str) -> Path:
            if path == self.ui:
                raise PermissionError("denied")
            return stage_replacement(path, data, label)

        with (
            patch("aside_patcher.transaction.stage_replacement", side_effect=fail_ui),
            self.assertRaises(Refusal),
        ):
            replace_transaction(changes)
        assert_transaction_unchanged(self)

    def test_second_replacement_failure_rolls_back_first(self):
        changes = transaction_changes(self)
        real_replace = os.replace

        def fail_ui(src: Path, dst: Path) -> None:
            if dst == self.ui and str(src).endswith(".new"):
                raise PermissionError("denied")
            real_replace(src, dst)

        with (
            patch("aside_patcher.transaction.os.replace", side_effect=fail_ui),
            self.assertRaises(Refusal),
        ):
            replace_transaction(changes)
        assert_transaction_unchanged(self)

    def test_changed_file_during_staging_refuses(self):
        changes = {self.dll: (self.original["dll"], b"new DLL")}
        with (
            patch("aside_patcher.transaction.file_sha256", return_value="different"),
            self.assertRaises(Refusal),
        ):
            replace_transaction(changes)
        self.assertEqual(self.dll.read_bytes(), self.original["dll"])

    def test_failed_rollback_keeps_recovery_copy(self):
        changes = transaction_changes(self)
        real_replace = os.replace

        def fail_replace(src: Path, dst: Path) -> None:
            if dst == self.ui or str(src).endswith(".rollback"):
                raise PermissionError("denied")
            real_replace(src, dst)

        with (
            patch("aside_patcher.transaction.os.replace", side_effect=fail_replace),
            self.assertRaisesRegex(Refusal, "recovery copy"),
        ):
            replace_transaction(changes)
        recovery = list(self.root.glob(".Aside.dll.*.rollback"))
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0].read_bytes(), self.original["dll"])
