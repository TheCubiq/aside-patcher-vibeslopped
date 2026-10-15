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


class TestReleaseGate(unittest.TestCase):
    """Use temporary documents and disposable keys without publishing anything."""

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
