"""Keep compatibility publication bound to reviewed support and runtime evidence."""

import argparse
import base64
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aside_patcher.common.utils import sha256
from aside_patcher.compatibility import canonical
from scripts import compatibility_release as release
from tests.test_catalog_refresh import synthetic_package


class TestReleaseGate(unittest.TestCase):
    """Use temporary documents and disposable keys without publishing anything."""

    def test_analysis_cache_refuses_partial_or_changed_output(self):
        """A prior input match must not hide missing or corrupted candidate files."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = {"engine_version": 1}
            analysis = root / "analysis.json"
            self.assertFalse(release.reuse_analysis(analysis, identity))
            analysis.write_text(json.dumps({"identity": identity, "patches": [{"analysis_passed": True}]}))
            with self.assertRaisesRegex(ValueError, "incomplete"):
                release.reuse_analysis(analysis, identity)
            data = b"candidate"
            (root / "package.unsigned.json").write_bytes(data)
            (root / "candidate.json").write_text(json.dumps({"candidate_sha256": sha256(data)}))
            self.assertTrue(release.reuse_analysis(analysis, identity))
            (root / "package.unsigned.json").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "changed"):
                release.reuse_analysis(analysis, identity)

    def test_file_checks_cannot_qualify_as_runtime_acceptance(self):
        """Reject an exact candidate whose interactive runtime report did not pass."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = canonical({"builds": [{"states": [{"targets": []}]}]})
            (root / "package.unsigned.json").write_bytes(package)
            candidate = {"candidate_sha256": sha256(package), "required_runtime_checks": ["launch"]}
            (root / "candidate.json").write_text(json.dumps(candidate))
            runtime = dict(candidate, runtime_passed=False, checks={"launch": True}, combinations=[{"targets": [], "passed": True}])
            (root / "runtime.json").write_text(json.dumps(runtime))
            args = argparse.Namespace(candidate=root, runtime=root / "runtime.json")
            with self.assertRaisesRegex(ValueError, "interactive runtime checks"):
                release._tested_digest(args)  # pylint: disable=protected-access

            runtime["runtime_passed"] = True
            (root / "runtime.json").write_text(json.dumps(runtime))
            with self.assertRaisesRegex(ValueError, "interactive runtime checks"):
                release._tested_digest(args)  # pylint: disable=protected-access

            runtime["checks"] = dict.fromkeys(release.REQUIRED_RUNTIME_CHECKS, True)
            (root / "runtime.json").write_text(json.dumps(runtime))
            self.assertEqual(release._tested_digest(args), sha256(package))  # pylint: disable=protected-access

    def test_catalog_preserves_other_builds_and_rejects_rollback(self):
        """A new package must not silently remove another supported browser build."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
            (root / "public.pem").write_bytes(public)
            old = {"sha256": "old", "url": "old-release", "versions": ["1.0.922.1"]}
            document = {"schema_version": 1, "minimum_engine_version": 1, "sequence": 1, "packages": [old]}
            envelope = {"signed": document, "signature": base64.b64encode(key.sign(canonical(document))).decode()}
            (root / "catalog.json").write_bytes(canonical(envelope))
            args = argparse.Namespace(previous_catalog=root / "catalog.json", public=root / "public.pem", sequence=2)
            new = {"versions": ["1.0.928.2"]}
            self.assertEqual(release._catalog_packages(args, new), [old, new])  # pylint: disable=protected-access
            args.sequence = 1
            with self.assertRaisesRegex(ValueError, "sequence must advance"):
                release._catalog_packages(args, new)  # pylint: disable=protected-access

    def test_short_smoke_gate_requires_transactions_effects_and_intact_screenshots(self):
        """A short smoke plan still binds all combinations and captured evidence."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = canonical(synthetic_package())
            (root / "package.unsigned.json").write_bytes(package)
            digest = sha256(package)
            candidate = {
                "schema_version": 2,
                "candidate_sha256": digest,
                "runtime_selections": [["example"]],
                "required_runtime_checks": list(release.REQUIRED_RUNTIME_CHECKS),
            }
            (root / "candidate.json").write_text(json.dumps(candidate))
            image = root / "patched.png"
            image.write_bytes(b"fixture screenshot")
            runtime = {
                "candidate_sha256": digest,
                "runtime_passed": True,
                "restored": True,
                "checks": dict.fromkeys(release.REQUIRED_RUNTIME_CHECKS, True),
                "transactions": [{"targets": [], "passed": True}, {"targets": ["example"], "passed": True}],
                "combinations": [
                    {
                        "targets": ["example"],
                        "passed": True,
                        "screenshots": [{"path": "patched.png", "sha256": sha256(image.read_bytes())}],
                    }
                ],
            }
            (root / "runtime.json").write_text(json.dumps(runtime))
            args = argparse.Namespace(candidate=root, runtime=root / "runtime.json")
            self.assertEqual(release._tested_digest(args), digest)  # pylint: disable=protected-access
            image.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "screenshots"):
                release._tested_digest(args)  # pylint: disable=protected-access

    def test_failing_patch_does_not_block_a_passing_subset(self):
        """Filter failed patches while requiring fresh evidence for the reduced package."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = synthetic_package()
            package["patches"].append(dict(package["patches"][0], id="other"))
            state = {
                "targets": ["other"],
                "sha256": {"dll": sha256(b"Xbc")},
                "payloads": {
                    "dll": {
                        "original_sha256": sha256(b"abc"),
                        "sha256": sha256(b"Xbc"),
                        "size": 3,
                        "segments": [{"offset": 0, "data": base64.b64encode(b"X").decode()}],
                    }
                },
            }
            package["builds"][0]["states"].append(state)
            data = canonical(package)
            (root / "package.unsigned.json").write_bytes(data)
            candidate = {"schema_version": 2, "browser_version": "1.0.928.2", "candidate_sha256": sha256(data)}
            (root / "candidate.json").write_text(json.dumps(candidate))
            runtime = {
                "candidate_sha256": sha256(data),
                "restored": True,
                "combinations": [{"targets": ["example"], "passed": True}, {"targets": ["other"], "passed": False}],
            }
            (root / "runtime.json").write_text(json.dumps(runtime))
            output = root / "passing"
            release.passing_subset(argparse.Namespace(candidate=root, runtime=root / "runtime.json", output=output))
            filtered = json.loads((output / "package.unsigned.json").read_text())
            self.assertEqual([state["targets"] for state in filtered["builds"][0]["states"]], [[], ["example"]])
            self.assertEqual(filtered["patches"][1]["status"], "awaiting validation")
            self.assertFalse(json.loads((output / "candidate.json").read_text())["runtime_passed"])
