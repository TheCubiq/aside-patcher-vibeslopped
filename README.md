# Aside UI Patcher

Small standard-library-only Python tooling for narrowly patching the supported
Aside native binary and settings bundle. It uses explicit patch recipes,
SHA-256 allowlists, hash-keyed immutable backups, and staged file replacement
with rollback if an installation fails partway.

## Supported input

- Aside `1.0.928.2`
- `C:\Program Files\Aside\Application\1.0.928.2\Aside.dll`
- Original SHA-256: `9CDC4B08D095ABB48FDFEC2BABF7BE54ED35EB67EC20AF9341D4CF5302E5D510`
- Settings component `1.26.1010.2149`, active user-data asset
  `AsideAgentManager\1.26.1010.2149\assets\appearance-MpyMFkED.js`.

Every unsupported file hash or component version is refused. Later Aside builds
and independently updated settings bundles must be inspected and added explicitly.

## Current patch availability

- `chats-toggle`: enables the **Chats** checkbox in **Appearance → Customize
  sidebar**, backed by its own persistent profile preference. Starts hidden;
  changes immediately in all windows. Bookmarks stays independent.
- `chats`: permanently hides the complete section, as in the original patch.
- `ask-aside`: keeps the toolbar button hidden.

`chats-toggle` and `chats` are mutually exclusive. Selecting either replaces
the other and preserves the Ask Aside patch. Supplying both in one command is
refused. The native toggle adds a small read/execute PE section, native registry
and observer hooks, and chained unwind records. See `docs/RESEARCH.md` for evidence.

## Commands

From this directory:

```powershell
uv run python -m aside_patcher status
uv run python -m aside_patcher apply --target chats-toggle --target ask-aside
uv run python -m aside_patcher apply --target chats-toggle
uv run python -m aside_patcher apply --target chats
uv run python -m aside_patcher apply --target chats --target ask-aside
uv run python -m aside_patcher restore
```

Close Aside before applying or restoring. Run these commands from an elevated
terminal for this Program Files installation. Backups are stored outside Git at
`%LOCALAPPDATA%\AsidePatcher\backups\<original-sha256>.<extension>` and are never
overwritten with different contents. Restore returns both files to vendor
originals and accepts only a known installed state and verified original backups.
It leaves the stored Chats preference intact; reinstalling the toggle reuses
your choice.

Patching changes the vendor-signed DLL and its hash. Each installed patch
must be checked in a fresh Aside launch, on an ordinary page and in Settings;
restore the original if launch or UI verification fails.

The hidden `--binary` override is supported only by legacy single-file manifests.
For copied multi-file trials, use an isolated manifest with copied file paths;
the supported hashes remain mandatory.

## Patch definition requirements

For each independently selectable target, add a patch only after static and
runtime validation. Each file has its original hash and target recipes in
`aside_patcher/builds.json`; every supported target combination lists the exact
hash of every file. Simple edits use unique byte sites; native code additions
use a named, version-specific standard-library builder. New builders must be
registered explicitly in `installation.py`. Rebuild desired states from verified
original backups, validate all output hashes, and test copies before installation.

## Tests

```powershell
uv run python -m unittest discover -s tests -v
```

Python 3.10 or newer is required. The patcher uses only the standard library.

## Development

Install [uv](https://docs.astral.sh/uv/) and run `uv sync` from this directory.
The committed `uv.lock` pins development tools; the patcher has no runtime
dependencies. Python 3.10 or newer remains supported.

```powershell
uv run black aside_patcher tests
uv run black --check aside_patcher tests
uv run flake8 aside_patcher tests
uv run pylint aside_patcher tests
```

Black formats code with a maximum line length of 135 characters; Flake8 and
Pylint use the same limit. Flake8 catches style and unused code;
Pylint adds code-quality checks. Keep code readable, with narrow exceptions for
native patch recipes and test fixtures rather than splitting safety checks just
to satisfy complexity limits.

## Documentation and audit

Use NumPy-style docstrings for patcher functions and classes, including
`Parameters` and `Returns` where relevant. Keep summaries concrete and document
safety constraints. Type hints belong in Python signatures too.

Flake8 enforces the NumPy convention through `flake8-docstrings`. Numpydoc checks
parameter coverage, section structure, types, descriptions, and return sections
without importing or executing the patcher. Test names describe assertions;
test functions are exempt from docstring requirements.

Run every quality check with:

```powershell
.\scripts\check.ps1
```

Or inspect the full codebase audit directly:

```powershell
uv run mollify audit
```

Mollify is a locked development dependency. Every audit category fails the
quality gate on findings, including complexity and duplication. No audit rules
are suppressed. Keep refactors readable and preserve the verified native output.

The check script runs formatting, lint, and documentation checks, then Mollify.
It does not run unit tests or modify the installed Aside files.

## Code structure

- `manifest.py` parses supported builds without making the patching modules
  depend on each other.
- `core.py` contains shared hashing and file safety utilities.
- `legacy.py` contains the supported single-file patch path.
- `processes.py` checks whether Aside is running before file modification.
- `installation.py` separates state selection, rendering, staging, and rollback.
- `native_toggle.py` assembles the verified recipe in named phases: preference
  registration, refresh observer, bridge hooks, visibility, schema, unwind
  records, and final PE section installation.

The native instruction listings remain version-specific. Refactoring them must
preserve the allowlisted output byte for byte. Unit tests use conventional
`Test*` class names for both unittest discovery and static audit reachability.
