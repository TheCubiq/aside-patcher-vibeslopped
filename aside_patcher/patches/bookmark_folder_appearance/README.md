# Bookmark folder appearance

Right-click a bookmark folder to choose one of Aside's eight native group colors,
open the native 48-icon picker, or reset both choices. Each folder and subfolder
has independent appearance. Expanded folders show a rounded tinted block around
their visible contents; nested hover and selection backgrounds respect depth.

```powershell
uv run python -m aside_patcher enable bookmark-folder-appearance
uv run python -m aside_patcher disable bookmark-folder-appearance
```

The installer preserves other enabled patches. Supported combinations include
Ask Aside and either Chats toggle or permanent Chats hiding. Close Aside first;
the Program Files installation requires an elevated terminal.

Appearance is stored in the local profile preference
`aside.bookmark_folder_appearance`, keyed by folder GUID. Rename and move retain
it, subtree deletion removes it, and disabling the patch retains it for later
re-enabling. Bookmark titles, URLs, hierarchy, manager rendering and native APIs
are unchanged. Policy-managed folders retain their original context menu.

The patch supports only the exact allowlisted Aside 1.0.928.2 image. Its frozen
payload and native sources are hash-pinned. The payload uses separate RX code and
RW state sections, extends the existing exception and GuardCF tables, and uses
the shared immutable-backup and transactional-restoration machinery.

To rebuild the payload, compile every `native_*.c` in this directory using the
same AMD64 MSVC toolset with `/O2 /W4 /WX /GS- /guard:cf /Zl`, then run:

```powershell
uv run python scripts/freeze_bookmark_payload.py <object-directory>
```

Update the manifest pin in `native.py`, regenerate all candidate images, and
repeat fresh-runtime validation before changing the installation allowlist.
Compile the storage harness in a separate directory so its objects cannot replace
production objects. Investigation and verification details are in
`docs/BOOKMARK_FOLDER_APPEARANCE.md`.
