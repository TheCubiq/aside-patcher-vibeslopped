"""Authenticate catalog data and reject corrupted or unsafe binary deltas."""

import base64
import json
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.utils import sha256
from aside_patcher.compatibility import apply_delta, canonical, verify_document


class TestCompatibility(unittest.TestCase):
    """Use ephemeral test keys; production trust must never depend on these."""

    def test_malformed_signed_headers_are_refused(self):
        """Refuse malformed authenticated shapes without leaking parser failures."""
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        documents = [
            [],
            {"schema_version": True, "minimum_engine_version": 1},
            {"schema_version": 1, "minimum_engine_version": False},
            {"schema_version": 1, "minimum_engine_version": 0},
            {"schema_version": 1, "minimum_engine_version": 1, "expires_at": "2030-01-01T00:00:00"},
        ]
        for document in documents:
            with self.subTest(document=document):
                envelope = {"signed": document, "signature": base64.b64encode(key.sign(canonical(document))).decode()}
                with self.assertRaises(Refusal):
                    verify_document(canonical(envelope), public)
        with self.assertRaises(Refusal):
            verify_document(b"[]", public)

    def test_signed_document_and_tamper_detection(self):
        """Accept a genuine signed document and reject a modified payload."""
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        document = {"schema_version": 1, "minimum_engine_version": 1, "sequence": 1}
        envelope = {"signed": document, "signature": base64.b64encode(key.sign(canonical(document))).decode()}
        self.assertEqual(verify_document(json.dumps(envelope).encode(), public), document)
        envelope["signed"]["sequence"] = 2
        with self.assertRaises(Refusal):
            verify_document(json.dumps(envelope).encode(), public)

    def test_exact_original_delta_and_output_guards(self):
        """Reconstruct data without accepting different originals or offsets."""
        recipe = {
            "original_sha256": sha256(b"abc"),
            "sha256": sha256(b"aXYZ"),
            "size": 4,
            "segments": [{"offset": 1, "data": base64.b64encode(b"XYZ").decode()}],
        }
        self.assertEqual(apply_delta(b"abc", recipe), b"aXYZ")
        with self.assertRaises(Refusal):
            apply_delta(b"abd", recipe)
        recipe["segments"][0]["offset"] = 2
        with self.assertRaises(Refusal):
            apply_delta(b"abc", recipe)

    def test_expired_support_requires_explicit_cached_policy(self):
        """Keep verified offline support without accepting expired new downloads."""
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        document = {"schema_version": 1, "minimum_engine_version": 1, "expires_at": "2000-01-01T00:00:00+00:00"}
        data = canonical({"signed": document, "signature": base64.b64encode(key.sign(canonical(document))).decode()})
        with self.assertRaises(Refusal):
            verify_document(data, public)
        self.assertEqual(verify_document(data, public, allow_expired=True), document)
        with self.assertRaises(Refusal):
            verify_document(
                data,
                Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo),
                allow_expired=True,
            )

    def test_shared_payloads_and_original_copies_are_bounded(self):
        """Reconstruct shared and copied bytes without accepting invalid sources."""
        digest = sha256(b"XYZ")
        recipe = {
            "original_sha256": sha256(b"abc"),
            "sha256": sha256(b"abcXYZabc"),
            "size": 9,
            "blobs": {digest: base64.b64encode(b"XYZ").decode()},
            "segments": [{"offset": 3, "data_ref": digest}, {"offset": 6, "copy_offset": 0, "size": 3}],
        }
        self.assertEqual(apply_delta(b"abc", recipe), b"abcXYZabc")
        recipe["segments"][1]["copy_offset"] = 1
        with self.assertRaises(Refusal):
            apply_delta(b"abc", recipe)
        recipe["segments"][1]["copy_offset"] = 0
        recipe["blobs"][digest] = base64.b64encode(b"BAD").decode()
        with self.assertRaises(Refusal):
            apply_delta(b"abc", recipe)
