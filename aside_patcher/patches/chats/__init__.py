"""Permanently hide the Chats sidebar section."""

from pathlib import Path

from ...patch_loader import load_definition

PATCH = load_definition(Path(__file__).parent)
