"""Exercise signed catalog updates without network access or browser binaries."""

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.utils import sha256
from aside_patcher.compatibility import CATALOG_URL, apply_delta, bundled_build, cached_build, canonical, refresh


def signed_document(document: dict, key: Ed25519PrivateKey) -> bytes:
    """Sign a synthetic document with a disposable test key."""
    signature = base64.b64encode(key.sign(canonical(document))).decode()
    return canonical({"signed": document, "signature": signature})


def synthetic_package() -> dict:
    """Describe one small artifact and two fully hash-guarded states."""
    before = sha256(b"abc")
    after = sha256(b"abZ")
    original_recipe = {
        "original_sha256": before,
        "sha256": before,
        "size": 3,
        "segments": [],
    }
    changed_recipe = {
        "original_sha256": before,
        "sha256": after,
        "size": 3,
        "segments": [{"offset": 2, "data": base64.b64encode(b"Z").decode()}],
    }
    return {
        "schema_version": 1,
        "minimum_engine_version": 1,
        "patches": [
            {
                "id": "example",
                "description": "Synthetic patch",
                "status": "supported",
                "conflicts": [],
                "files": ["dll"],
            }
        ],
        "builds": [
            {
                "version": "1.0.928.2",
                "files": {"dll": {"path": "Aside.dll", "original_sha256": before}},
                "states": [
                    {"targets": [], "sha256": {"dll": before}, "payloads": {"dll": original_recipe}},
                    {"targets": ["example"], "sha256": {"dll": after}, "payloads": {"dll": changed_recipe}},
                ],
            }
        ],
    }


class TestCatalogRefresh(unittest.TestCase):
    """Verify the cache transaction independently of vendor files and owner keys."""

    def test_source_bundled_adapters_are_hash_pinned(self):
        """Offline bundled definitions use the same parser and reject altered packages."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / "compatibility/bundled"
            bundled.mkdir(parents=True)
            package = canonical(synthetic_package())
            (bundled / "1.0.928.2.json").write_bytes(package)
            (bundled / "index.json").write_bytes(canonical({"builds": {"1.0.928.2": {"sha256": sha256(package)}}}))
            with patch("aside_patcher.compatibility.__file__", str(root / "aside_patcher/compatibility.py")):
                self.assertEqual(bundled_build("1.0.928.2").version, "1.0.928.2")
                self.assertIsNone(bundled_build("1.0.922.1"))
                (bundled / "1.0.928.2.json").write_bytes(package + b" ")
                with self.assertRaisesRegex(Refusal, "digest mismatch"):
                    bundled_build("1.0.928.2")

    def test_verified_cache_survives_offline_corruption_and_rollback(self):
        """Only a complete verified update may replace the active catalog."""
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        package = signed_document(synthetic_package(), key)
        package_url = "https://github.com/TheCubiq/aside-patcher-vibeslopped/releases/download/test/package.json"
        catalog = {
            "schema_version": 1,
            "minimum_engine_version": 1,
            "sequence": 1,
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "packages": [{"sha256": sha256(package), "url": package_url}],
        }
        original_catalog = signed_document(catalog, key)
        downloads = {CATALOG_URL: original_catalog, package_url: package}

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("aside_patcher.compatibility._public_key", return_value=public),
            patch("aside_patcher.compatibility._download", side_effect=downloads.__getitem__) as download,
        ):
            cache = Path(directory)
            self.assertEqual(refresh(cache)["packages"], 1)
            build = cached_build(cache, "1.0.928.2")
            self.assertEqual(apply_delta(b"abc", build.state_payloads[("example",)]["dll"]), b"abZ")

            download.side_effect = OSError("offline")
            with self.assertRaises(OSError):
                refresh(cache)
            self.assertEqual(cached_build(cache, "1.0.928.2").states, build.states)

            catalog["sequence"] = 2
            downloads[CATALOG_URL] = signed_document(catalog, key)
            downloads[package_url] = b"corrupt download"
            download.side_effect = downloads.__getitem__
            with self.assertRaisesRegex(Refusal, "digest mismatch"):
                refresh(cache)
            self.assertEqual((cache / "catalog.json").read_bytes(), original_catalog)

            invalid_package = synthetic_package()
            invalid_package["builds"][0]["states"][1]["targets"] = ["unavailable"]
            downloads[package_url] = signed_document(invalid_package, key)
            catalog["packages"][0]["sha256"] = sha256(downloads[package_url])
            downloads[CATALOG_URL] = signed_document(catalog, key)
            with self.assertRaisesRegex(Refusal, "unavailable patch"):
                refresh(cache)
            self.assertEqual((cache / "catalog.json").read_bytes(), original_catalog)

            catalog["sequence"] = 0
            downloads[CATALOG_URL] = signed_document(catalog, key)
            with self.assertRaisesRegex(Refusal, "rollback"):
                refresh(cache)
            self.assertEqual((cache / "catalog.json").read_bytes(), original_catalog)
