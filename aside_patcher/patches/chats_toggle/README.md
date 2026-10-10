# chats-toggle

Enable the persistent Chats visibility checkbox in Appearance settings.

Supported browser: Aside `1.0.928.2`. `patch.json` records the exact
original and standalone patched file hashes, replacement sites, and conflicts.
The root build manifest allowlists complete multi-patch output combinations.

Enable: `uv run python -m aside_patcher enable chats-toggle`
Disable: `uv run python -m aside_patcher disable chats-toggle`

Files are rebuilt from verified originals; disabling retains other enabled
patches. Immutable backups and rollback are handled by the shared installer.

`native.py` orchestrates the native recipe. `instructions.py` contains the x64
hook listings. `bridge.py` extends the browser preference schema and installs
verified hook addresses. The two `appearance.*.js` files preserve the exact
before/after settings component text; their bytes must not be reformatted.

Chats starts hidden when its profile preference is absent. Disabling this mod
restores the vendor settings UI; it does not erase the saved preference.
