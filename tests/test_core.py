from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch as mock_patch
from pathlib import Path

from aside_patcher.core import (
    Build,
    Patch,
    PatchSite,
    Refusal,
    apply_to_bytes,
    atomic_replace,
    ensure_immutable_backup,
    restore_bytes,
    sha256,
)
from aside_patcher.cli import run_apply


def fixture_build(original: bytes, first: Patch, second: Patch) -> Build:
    first_result = bytearray(original)
    for site in first.sites:
        first_result[site.offset : site.offset + len(site.before)] = site.after
    second_result = bytearray(original)
    for site in second.sites:
        second_result[site.offset : site.offset + len(site.before)] = site.after
    combined = bytearray(original)
    for patch in (first, second):
        for site in patch.sites:
            combined[site.offset : site.offset + len(site.before)] = site.after
    states = {
        sha256(original): (),
        sha256(bytes(first_result)): (first.target,),
        sha256(bytes(second_result)): (second.target,),
        sha256(bytes(combined)): (first.target, second.target),
    }
    return Build(
        "test",
        Path("test.dll"),
        sha256(original),
        states,
        {first.target: first, second.target: second},
    )


class PatcherCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = b"prefix-CHAT0-middle-ASK0-suffix"
        self.chats = Patch(
            "chats", "hide chat section", (PatchSite(7, b"CHAT0", b"CHAT1"),)
        )
        self.ask = Patch(
            "ask-aside", "hide toolbar action", (PatchSite(20, b"ASK0", b"ASK1"),)
        )
        self.build = fixture_build(self.original, self.chats, self.ask)

    def test_targets_apply_independently_and_together(self) -> None:
        chats_only = apply_to_bytes(self.original, self.build, ["chats"])
        self.assertEqual(chats_only[7:12], b"CHAT1")
        self.assertEqual(chats_only[20:24], b"ASK0")

        ask_only = apply_to_bytes(self.original, self.build, ["ask-aside"])
        self.assertEqual(ask_only[7:12], b"CHAT0")
        self.assertEqual(ask_only[20:24], b"ASK1")

        both = apply_to_bytes(self.original, self.build, ["chats", "ask-aside"])
        self.assertEqual(both[7:12], b"CHAT1")
        self.assertEqual(both[20:24], b"ASK1")
        self.assertEqual(apply_to_bytes(chats_only, self.build, ["ask-aside"]), both)
        self.assertEqual(apply_to_bytes(ask_only, self.build, ["chats"]), both)

    def test_cli_sequential_apply_preserves_original_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "Aside.dll"
            binary.write_bytes(self.original)
            with (
                mock_patch("aside_patcher.cli.require_app_stopped"),
                mock_patch(
                    "aside_patcher.cli.backup_directory", return_value=root / "backups"
                ),
            ):
                run_apply(self.build, binary, ["chats"])
                run_apply(self.build, binary, ["ask-aside"])
            backup = root / "backups" / f"{sha256(self.original)}.dll"
            self.assertEqual(backup.read_bytes(), self.original)
            backup.chmod(0o644)

    def test_permission_failure_leaves_binary_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "Aside.dll"
            binary.write_bytes(self.original)
            with mock_patch(
                "aside_patcher.core.os.replace", side_effect=PermissionError("denied")
            ):
                with self.assertRaisesRegex(Refusal, "Could not replace"):
                    atomic_replace(binary, b"replacement")
            self.assertEqual(binary.read_bytes(), self.original)
            self.assertEqual(list(Path(temp).iterdir()), [binary])

    def test_running_process_refuses_before_reading_binary(self) -> None:
        with mock_patch("aside_patcher.core.aside_is_running", return_value=True):
            with self.assertRaisesRegex(Refusal, "Aside is running"):
                run_apply(self.build, Path("missing.dll"), ["chats"])

    def test_permission_failure_on_temp_creation_is_immediate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "Aside.dll"
            binary.write_bytes(self.original)
            with mock_patch.object(
                Path, "open", side_effect=PermissionError("denied")
            ) as opened:
                with self.assertRaisesRegex(Refusal, "Could not replace"):
                    atomic_replace(binary, b"replacement")
                self.assertEqual(opened.call_count, 1)
            self.assertEqual(binary.read_bytes(), self.original)

    def test_rejects_unknown_hash(self) -> None:
        with self.assertRaisesRegex(Refusal, "Unsupported binary hash"):
            apply_to_bytes(b"unknown", self.build, ["chats"])

    def test_multiple_sites_apply_as_one_target(self) -> None:
        chats = Patch(
            "chats", "two visibility paths", self.chats.sites + self.ask.sites
        )
        both = bytearray(self.original)
        both[7:12] = b"CHAT1"
        both[20:24] = b"ASK1"
        build = Build(
            "test",
            Path("test.dll"),
            sha256(self.original),
            {sha256(self.original): (), sha256(both): ("chats",)},
            {"chats": chats},
        )
        self.assertEqual(apply_to_bytes(self.original, build, ["chats"]), bytes(both))
        self.assertEqual(apply_to_bytes(bytes(both), build, ["chats"]), bytes(both))

    def test_invalid_second_site_refuses_complete_target(self) -> None:
        chats = Patch(
            "chats",
            "invalid second path",
            self.chats.sites + (PatchSite(20, b"MISSING", b"CHANGED"),),
        )
        build = Build(
            "test",
            Path("test.dll"),
            sha256(self.original),
            self.build.states,
            {"chats": chats},
        )
        with self.assertRaisesRegex(Refusal, "expected one exact byte match"):
            apply_to_bytes(self.original, build, ["chats"])
        self.assertEqual(self.original[7:12], b"CHAT0")

    def test_rejects_ambiguous_byte_match(self) -> None:
        ambiguous = self.original + b"CHAT0"
        ambiguous_build = Build(
            self.build.version,
            self.build.binary_path,
            sha256(ambiguous),
            {sha256(ambiguous): ()},
            self.build.patches,
        )
        with self.assertRaisesRegex(Refusal, "expected one exact byte match"):
            apply_to_bytes(ambiguous, ambiguous_build, ["chats"])

    def test_backup_is_hash_keyed_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            backup = ensure_immutable_backup(self.original, self.build, Path(temp))
            self.assertEqual(backup.name, f"{sha256(self.original)}.dll")
            self.assertEqual(backup.read_bytes(), self.original)
            backup.chmod(0o644)
            backup.write_bytes(b"wrong data")
            with self.assertRaisesRegex(Refusal, "will not be overwritten"):
                ensure_immutable_backup(self.original, self.build, Path(temp))

    def test_restore_is_byte_for_byte_and_requires_known_patched_state(self) -> None:
        patched = apply_to_bytes(self.original, self.build, ["chats", "ask-aside"])
        self.assertEqual(
            restore_bytes(patched, self.original, self.build), self.original
        )
        with self.assertRaisesRegex(Refusal, "unknown installed hash"):
            restore_bytes(b"unknown", self.original, self.build)
        with self.assertRaisesRegex(Refusal, "does not match"):
            restore_bytes(patched, b"corrupt backup", self.build)

    def test_unconfigured_targets_refuse_without_producing_output(self) -> None:
        unconfigured = Build(
            self.build.version,
            self.build.binary_path,
            self.build.original_sha256,
            self.build.states,
            {},
        )
        with self.assertRaisesRegex(Refusal, "No verified patch"):
            apply_to_bytes(self.original, unconfigured, ["chats"])
        self.assertEqual(self.original, b"prefix-CHAT0-middle-ASK0-suffix")


if __name__ == "__main__":
    unittest.main()
