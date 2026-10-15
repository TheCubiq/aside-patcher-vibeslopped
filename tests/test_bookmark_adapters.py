"""Check exact-build native adapter integrity without vendor binaries."""

import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.utils import sha256
from aside_patcher.patches.bookmark_folder_appearance import adapters


class TestBookmarkAdapters(unittest.TestCase):
    """Reject changed inputs, manifests, payloads and source templates."""

    def setUp(self):
        # unittest owns cleanup through enterContext, including setup failures.
        # pylint: disable-next=consider-using-with
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.document = {
            "schema_version": 1,
            "original_sha256": sha256(b"vendor"),
            "template_sources": {"native.c": sha256(b"template")},
        }
        self.payload = {
            "metadata": {"sha256": sha256(b"code"), "code_size": 4, "data_size": 0},
            "blob_base64": base64.b64encode(b"code").decode(),
        }
        (self.root / "native.c").write_bytes(b"template")
        self.write_document()
        self.addCleanup(patch.stopall)
        patch.object(adapters, "ROOT", self.root).start()
        patch.object(adapters, "__file__", str(self.root / "adapters.py")).start()

    def write_document(self):
        """Pin a synthetic adapter and its frozen payload separately."""
        frozen = json.dumps(self.payload).encode()
        (self.root / "payload.json").write_bytes(frozen)
        self.document.update(payload_file="payload.json", payload_sha256=sha256(frozen))
        data = json.dumps(self.document).encode()
        (self.root / "build.json").write_bytes(data)
        index = {"originals": {sha256(b"vendor"): {"file": "build.json", "sha256": sha256(data)}}}
        (self.root / "index.json").write_text(json.dumps(index))

    def test_exact_input_loads(self):
        """Known original and matching artifacts load successfully."""
        self.assertEqual(adapters.load_adapter(b"vendor")[2], b"code")

    def test_unknown_input_is_rejected(self):
        """Nearby versions cannot borrow another build's bindings."""
        with self.assertRaisesRegex(Refusal, "No verified bookmark adapter"):
            adapters.load_adapter(b"other build")

    def test_changed_document_is_rejected(self):
        """The index binds the complete adapter contents."""
        (self.root / "build.json").write_text("{}")
        with self.assertRaisesRegex(Refusal, "digest mismatch"):
            adapters.load_adapter(b"vendor")

    def test_changed_payload_is_rejected(self):
        """A matching manifest cannot excuse changed compiled bytes."""
        self.payload["blob_base64"] = base64.b64encode(b"evil").decode()
        self.write_document()
        with self.assertRaisesRegex(Refusal, "payload integrity"):
            adapters.load_adapter(b"vendor")

    def test_changed_template_is_rejected(self):
        """Native source edits invalidate previously compiled adapters."""
        (self.root / "native.c").write_bytes(b"changed")
        with self.assertRaisesRegex(Refusal, "source template changed"):
            adapters.load_adapter(b"vendor")
