"""Native bookmark folder appearance payload and verified binary renderer."""

from pathlib import Path

from ...patch_loader import load_definition
from .native import build_native_appearance

PATCH = load_definition(Path(__file__).parent, {"dll": build_native_appearance})
