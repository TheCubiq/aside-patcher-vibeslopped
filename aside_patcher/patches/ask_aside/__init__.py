"""Hide the Ask Aside toolbar button."""

from pathlib import Path

from ...patch_loader import load_definition

PATCH = load_definition(Path(__file__).parent)
