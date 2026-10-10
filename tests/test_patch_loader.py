"""Check local patch definitions against the reviewed build registry."""

import unittest
from dataclasses import replace
from pathlib import Path

from aside_patcher.common.exceptions import Refusal
from aside_patcher.common.models import MANIFEST_PATH
from aside_patcher.manifest import load_build
from aside_patcher.patch_loader import attach_recipes, load_definition, load_sites, local_asset, patches_for_version
from aside_patcher.patches import PATCHES


class TestPatchLoader(unittest.TestCase):
    """Require explicit, internally consistent package metadata."""

    def test_registry_matches_build(self):
        """Attach every registered patch to its exact standalone hashes."""
        build = load_build(MANIFEST_PATH)
        self.assertEqual(set(build.targets), {definition.patch_id for definition in PATCHES})
        self.assertEqual(set(build.files["appearance"].recipes), {"chats-toggle"})

    def test_unknown_version_and_duplicate_ids_refuse(self):
        """Require an exact supported version and unique registration."""
        for version, definitions in (("unknown", PATCHES), ("1.0.928.2", PATCHES + PATCHES)):
            with self.assertRaises(Refusal):
                patches_for_version(version, definitions)

    def test_unknown_conflict_refuses(self):
        """Require conflicts to refer to registered patches."""
        definition = replace(PATCHES[0], conflicts=("missing",))
        with self.assertRaises(Refusal):
            patches_for_version("1.0.928.2", (definition,))

    def test_unverified_local_hash_refuses(self):
        """Reject metadata that disagrees with reviewed whole-file output."""
        build = load_build(MANIFEST_PATH)
        definition = PATCHES[0]
        files = {name: dict(recipe, patched_sha256="unknown") for name, recipe in definition.files.items()}
        with self.assertRaises(Refusal):
            attach_recipes(build.files, {definition.patch_id: replace(definition, files=files)}, build.states)

    def test_asset_escape_and_ambiguous_source_refuse(self):
        """Keep byte assets local and require one source per replacement side."""
        directory = Path(__file__).parent
        with self.assertRaises(Refusal):
            local_asset(directory, "../README.md")
        with self.assertRaises(Refusal):
            load_sites(directory, [{"before": "00", "before_file": "anything"}])

    def test_renderer_must_be_declared_and_callable(self):
        """Refuse unknown file IDs and noncallable native generators."""
        directory = MANIFEST_PATH.parent / "patches" / "chats"
        for renderers in ({"unknown": bytes}, {"dll": "unknown"}):
            with self.assertRaises(Refusal):
                load_definition(directory, renderers)
