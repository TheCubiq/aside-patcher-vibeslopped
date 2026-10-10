# chats

Permanently hide the Chats sidebar section.

Supported browser: Aside `1.0.928.2`. `patch.json` records the exact
original and standalone patched file hashes, replacement sites, and conflicts.
The root build manifest allowlists complete multi-patch output combinations.

Enable: `uv run python -m aside_patcher enable chats`
Disable: `uv run python -m aside_patcher disable chats`

Files are rebuilt from verified originals; disabling retains other enabled
patches. Immutable backups and rollback are handled by the shared installer.
