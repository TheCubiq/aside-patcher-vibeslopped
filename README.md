# Aside UI Patcher

Small standard-library-only Python tooling for narrowly patching the supported
Aside native binary. It uses explicit byte signatures, SHA-256 allowlists,
hash-keyed immutable backups, and atomic replacement.

## Supported input

- Aside `1.0.928.2`
- `C:\Program Files\Aside\Application\1.0.928.2\Aside.dll`
- Original SHA-256: `9CDC4B08D095ABB48FDFEC2BABF7BE54ED35EB67EC20AF9341D4CF5302E5D510`

Every other binary hash is refused. Later Aside builds must be inspected and
added explicitly.

## Current patch availability

Both targets have explicit patch definitions, verified through native WinDbg
runtime trials and against a copy of the exact supported DLL. Each target
changes two visibility paths. Chats hides the complete section; Ask Aside
hides the toolbar control. Bookmarks, Tabs, and Settings remain usable in the
runtime trial. See `docs/RESEARCH.md` for addresses and verification evidence.

## Commands

From this directory:

```powershell
python -m aside_patcher status
python -m aside_patcher apply --target chats
python -m aside_patcher apply --target chats --target ask-aside
python -m aside_patcher restore
```

Close Aside before applying or restoring. Run these commands from an elevated
terminal for this Program Files installation. Backups are stored outside Git at
`%LOCALAPPDATA%\AsidePatcher\backups\<original-sha256>.dll` and are never
overwritten with different contents. Restore accepts only a known patched state
and a backup matching the original SHA-256.

Patching changes the vendor-signed DLL and its hash. Each installed patch
must be checked in a fresh Aside launch, on an ordinary page and in Settings;
restore the original if launch or UI verification fails.

`--binary <path>` is an intentionally hidden testing override for working on a
copy; it does not change the supported-build allowlist.

## Patch definition requirements

For each independently selectable target, add a patch only after static and
runtime validation. Record each site's unique file offset, exact original bytes,
and same-length replacement bytes in `aside_patcher/builds.json`. Add the SHA-256
for every supported combination of selected targets to `states`. Test each
combination against a copy of the exact supported DLL before applying to the
installed file.

## Tests

```powershell
python -m unittest discover -s tests -v
```

Python 3.10 or newer is required. The patcher uses only the standard library.
