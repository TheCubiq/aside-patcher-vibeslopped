"""Enable the persistent Chats visibility checkbox in Appearance settings."""

from pathlib import Path

from ...patch_loader import load_definition
from .native import build_native_toggle

PATCH = load_definition(Path(__file__).parent, {"dll": build_native_toggle})
