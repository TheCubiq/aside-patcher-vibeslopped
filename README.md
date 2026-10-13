# Aside UI Patcher

Small standard-library-only Python tooling for narrowly patching the supported
Aside native binary and settings bundle. It uses explicit patch recipes,
SHA-256 allowlists, hash-keyed immutable backups, and staged file replacement
with rollback if an installation fails partway.

## Supported input

- Aside `1.0.928.2`
- `%ProgramFiles%\Aside\Application\1.0.928.2\Aside.dll` (typically `C:\Program Files\Aside\...`)
- Original SHA-256: `9CDC4B08D095ABB48FDFEC2BABF7BE54ED35EB67EC20AF9341D4CF5302E5D510`
- Settings component `1.26.1010.2149`, active user-data asset
  `%LOCALAPPDATA%\Aside\User Data\AsideAgentManager\1.26.1010.2149\assets\appearance-MpyMFkED.js`.

Every unsupported file hash or component version is refused. Later Aside builds
and independently updated settings bundles must be inspected and added explicitly.

## Current patch availability

- `chats-toggle`: enables the **Chats** checkbox in **Appearance → Customize
  sidebar**, backed by its own persistent profile preference. Starts hidden;
  changes immediately in all windows. Its divider hides with the section. Bookmarks stays independent.
- `chats`: permanently hides the complete section, as in the original patch.
- `ask-aside`: keeps the toolbar button hidden.
- `bookmark-folder-appearance`: adds native preset colors, the native icon picker,
  and reset to bookmark folder menus. Expanded folders receive rounded tinted
  blocks; nested hover highlights respect indentation. Choices persist by GUID
  locally per profile. See `docs/BOOKMARK_FOLDER_APPEARANCE.md`.

`chats-toggle` and `chats` are mutually exclusive. Previously verified revisions can be upgraded by enabling the same active targets.
Selecting either replaces
the other and preserves the Ask Aside patch. Supplying both in one command is
refused. The native toggle adds a small read/execute PE section, native registry
and observer hooks, and chained unwind records. See `docs/RESEARCH.md` for evidence.

## Commands

From this directory:

```powershell
uv run python -m aside_patcher list
uv run python -m aside_patcher status
uv run python -m aside_patcher enable chats-toggle ask-aside
uv run python -m aside_patcher enable chats-toggle
uv run python -m aside_patcher enable chats
uv run python -m aside_patcher enable chats ask-aside
uv run python -m aside_patcher disable ask-aside
uv run python -m aside_patcher disable chats-toggle
uv run python -m aside_patcher enable bookmark-folder-appearance
uv run python -m aside_patcher disable bookmark-folder-appearance
uv run python -m aside_patcher restore
```

Close Aside before applying or restoring. Run these commands from an elevated
terminal for this Program Files installation. Backups are stored outside Git at
`%LOCALAPPDATA%\AsidePatcher\backups\<original-sha256>.<extension>` and are never
overwritten with different contents. Restore returns both files to vendor
originals and accepts only a known installed state and verified original backups.
It leaves the stored Chats preference intact; reinstalling the toggle reuses
your choice.

Native-only changes can preserve the exact known settings artifact after its
active component updates. Changes to the settings artifact still require the
explicit supported component version. File hash guards apply to every operation.
Disabling folder appearance retains its saved profile preference.

If the independently updated settings bundle is unavailable, use
`uv run python -m aside_patcher --native-only enable bookmark-folder-appearance`
or `--native-only disable bookmark-folder-appearance`. This explicit mode checks
and changes only the supported DLL, preserves existing native patches, and leaves
settings files untouched. It also supports `--native-only status`; other patch
changes and restore require the complete supported installation.

Patching changes the vendor-signed DLL and its hash. Each installed patch
must be checked in a fresh Aside launch, on an ordinary page and in Settings;
restore the original if launch or UI verification fails.

For copied trials, use an isolated manifest with copied file paths (environment variables
like `%ProgramFiles%` and `%LOCALAPPDATA%` are expanded automatically); the supported
hashes remain mandatory. Commands operate on the complete file set in the manifest.

## Patch definition requirements

Each patch owns a directory under `aside_patcher/patches/`:

```text
patches/
  chats/
    __init__.py
    patch.json
    README.md
  ask_aside/
    __init__.py
    patch.json
    README.md
  chats_toggle/
    __init__.py
    patch.json
    README.md
    native.py
    instructions.py
    bridge.py
    constants.py
    appearance.before.js
    appearance.after.js
```

To add a patch:

1. Create a package directory; underscores in its name become hyphens in the CLI ID.
2. Declare its ID, description, exact supported versions, conflicts, and file recipes
   in `patch.json`. Each affected file declares its original and standalone patched
   SHA-256. Byte sites contain an offset and hexadecimal `before`/`after` bytes;
   `before_file`/`after_file` can reference exact local assets instead.
3. Export `PATCH = load_definition(Path(__file__).parent)` from `__init__.py`.
   For native generation, pass an explicit file-ID-to-callable renderer mapping.
   Keep version-specific helpers inside that patch directory.
4. Import the definition and add it to the explicit `PATCHES` tuple in
   `patches/__init__.py`. Registration is reviewed source code.
5. After static analysis, copied-file trials, and runtime validation, add exact
   output hashes for supported combinations to `builds.json`. That file owns
   installed paths, vendor hashes, component guards, and whole-installation states.
6. Add focused tests under `tests/patches/<package>/` and document verified behavior.

`list` shows registered patches and conflicts. `enable` merges selections with
active patches; a conflicting active patch is replaced. `disable` removes only
selected patches. Both rebuild from verified originals, check the complete output
against the allowlist, and use the same backup and rollback transaction. There is
no automatic patch discovery, hash approval, or update re-patching.

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
Pylint adds code-quality checks. Keep each module focused on one responsibility
and document the verified native instruction listings.

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

- `common/models.py` holds file and installation definitions, native structures,
  and shared constants.
- `common/utils.py` holds shared hashing, file reading, backup paths, byte matching,
  and process guards.
- `common/exceptions.py` defines the refusal exception.
- `manifest.py` accepts the verified multi-file manifest format.
- `installation.py` handles state selection and prepares installation changes.
- `patch_loader.py` reads local metadata and checks it against the build allowlist.
- `patches/` owns each selectable patch and its version-specific assets and helpers.
- `recipes.py` renders selected recipes and prepares verified replacements.
- `transaction.py` stages replacements and rolls back completed writes on failure.
- `pe.py` provides shared PE mapping and section operations.

The native instruction listings remain version-specific. Refactoring them must
preserve the allowlisted output byte for byte. Unit tests use conventional
`Test*` class names for both unittest discovery and static audit reachability.

The retired single-file implementation and its tests have been removed.
The remaining suites cover installation, backups, transactions, recipes, and
shared safety utilities using one temporary-installation fixture.
