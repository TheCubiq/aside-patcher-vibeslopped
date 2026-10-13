"""Explicitly register the bundled patch packages."""

from .ask_aside import PATCH as ASK_ASIDE
from .chats import PATCH as CHATS
from .chats_toggle import PATCH as CHATS_TOGGLE
from .bookmark_folder_appearance import PATCH as BOOKMARK_FOLDER_APPEARANCE

PATCHES = (CHATS, ASK_ASIDE, CHATS_TOGGLE, BOOKMARK_FOLDER_APPEARANCE)
