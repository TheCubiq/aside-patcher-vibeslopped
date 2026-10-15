"""Load exact-build bookmark adapters without executing downloaded code."""

import base64
import json
from pathlib import Path

from ...common.exceptions import Refusal
from ...common.utils import sha256
from ...patch_loader import local_asset

ROOT = Path(__file__).resolve().parents[3] / "compatibility/adapters"


class UnknownNativeBuild(Refusal):
    """No reviewed bindings exist; maintenance may attempt read-only discovery."""


def load_adapter(original: bytes) -> tuple[dict, dict, bytes]:
    """Require pinned adapter, source templates, metadata and compiled payload.

    Parameters
    ----------
    original : bytes
        Exact vendor DLL to identify without relying on its filename.

    Returns
    -------
    tuple of dict, dict and bytes
        Verified bindings, relocation metadata and compiled payload.
    """
    digest = sha256(original)
    index = json.loads((ROOT / "index.json").read_bytes())
    entry = index.get("originals", {}).get(digest)
    if entry is None:
        raise UnknownNativeBuild("No verified bookmark adapter for this original DLL.")
    document = local_asset(ROOT, entry["file"])
    if sha256(document) != entry["sha256"]:
        raise Refusal("Bookmark adapter digest mismatch.")
    adapter = json.loads(document)
    if adapter["schema_version"] != 1 or adapter["original_sha256"] != digest:
        raise Refusal("Bookmark adapter input identity mismatch.")
    frozen = local_asset(ROOT, adapter["payload_file"])
    if sha256(frozen) != adapter["payload_sha256"]:
        raise Refusal("Bookmark adapter payload document digest mismatch.")
    payload = json.loads(frozen)
    metadata = payload["metadata"]
    blob = base64.b64decode(payload["blob_base64"], validate=True)
    if sha256(blob) != metadata["sha256"] or len(blob) != metadata["code_size"] + metadata["data_size"]:
        raise Refusal("Bookmark adapter payload integrity check failed.")
    for name, expected in adapter["template_sources"].items():
        if sha256(local_asset(Path(__file__).parent, name)) != expected:
            raise Refusal(f"Bookmark adapter source template changed: {name}")
    return adapter, metadata, blob
