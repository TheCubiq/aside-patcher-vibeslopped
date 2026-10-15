# Desktop implementation

Approved target: Windows x64, GPUI Kit frontend, existing Python engine, official
version archive, independently validated patches, and owner-approved GitHub
compatibility releases. Developer prerequisites are acceptable initially.

## Delivery stages

- [x] Original installers and complete browser archives for 1.0.914.1,
  1.0.922.1, 1.0.928.1, and 1.0.928.2, with exact DLL versions,
  SHA-256 and valid publisher signatures; no installer executed.
- [ ] Full native adapter coverage. Cross-version toolbar analysis now supports
  verified subsets on .922.1 and .928.1; bookmark/settings adapters remain build-specific.
- [x] Multi-build discovery, compatibility metadata, structured JSON engine API
  implemented; new browser adapters still require independent validation.
- [x] GPUI UI, native UAC helper, graceful close, transactional recovery/restart
  implemented and locally exercised; broader acceptance gaps remain below.
- [ ] Release detection, deterministic analysis, interactive VM validation.
- [ ] Signed catalog/packages and publishing of exact tested artifacts after approval.
- [x] Second-machine protected Apply/UAC/restart/manual UI/Restore round trip
  for Hide Ask Aside plus Bookmark folder appearance on 1.0.928.2.
- [ ] Remaining second-machine UI cases and approved candidate-to-catalog validation.

## Constraints

Vendor archives live outside Git at `../tools/aside-archive` relative to the
native research tree (the acquisition command takes an explicit archive root).
Preserve CLI IDs, saved preferences, existing verified recipes, and immutable backups.
Unknown builds, ambiguous analysis, missing prerequisites, or failed tests never
qualify as supported. Validate each offered patch combination. Browser and settings
components evolve independently. Native launch/hash checks do not prove UI behavior.

Apply previews a complete selection, prepares verified outputs, closes Aside
gracefully (30-second timeout, Retry/Cancel), elevates only protected writes, locks
and revalidates inputs, installs with recovery, then restarts as the original user.
Never force-kill the browser or run the main UI elevated.

Catalog refresh runs on startup/manual request, with verified offline cache. No
background service or automatic browser closure. Packages contain declarative
recipes and compiled payloads, never downloaded Python programs. Sign packages and
catalog; publish only the approved immutable candidate. No vendor binaries in releases.

## Required external acceptance

A disposable interactive Windows VM, publishing/signing credentials, and another
Windows machine are needed to finish the approved acceptance criteria. Do not mark
these checks complete from unit tests or workflow presence.

On 2026-10-15 the owner confirmed a Windows 11 test machine is available later.
Second-machine acceptance is explicitly pending until that machine is connected;
it does not block remaining local implementation and isolated checks.

## Current evidence

- All 12 reviewed 1.0.928.2 selections passed actual JSON-engine file transactions
  on a separate original-browser copy, followed by verified restoration. This
  establishes file installation behavior, not native UI acceptance.
- Cross-version analysis found 10/16 unique hook candidates in 1.0.914.1 and
  14/16 in each later archived build, including the baseline. Generic destructor
  matches remain ambiguous. These reports authorize no new build support.
- GPUI window opens locally in the system's dark theme. Its own accessibility
  controls changed a selection and invoked preview; the rendered preview showed
  one affected file and the UAC requirement. Selection was returned to the
  installed state without applying changes.
- All 12 compact compatibility recipes reconstruct their exact reviewed hashes
  through the client's parser. The unsigned full package is 913,827 bytes; shared
  data is deduplicated and copied vendor tables reference the local original.
- A background-only synthetic browser blocked graceful shutdown and left its
  original DLL intact. This is a refusal check, not successful restart acceptance.
- Native Windows UAC applied a combined DLL/settings selection to an isolated
  Program Files fixture, then restored both originals exactly. The unelevated
  coordinator read the elevated helper's result successfully. Result files grant
  access to the initiating request owner through their open native handle.
  Evidence: `.patcher-work/protected-helper-native.json` (local, ignored).
- A harmless native UAC probe verified real cancellation with the operator
  choosing No. Cancellation preserved the `elevation_declined` result, while
  other launch failures have a separate `elevation_failed` code. No browser files
  were involved. Evidence: `.patcher-work/uac-cancellation-retry.json`.
- A fresh isolated browser completed graceful close/apply/restart and
  close/restore/restart cycles with identical captured profile arguments. Final
  shutdown and exact original DLL restoration passed. The lifecycle test uses
  its own synthetic profile so component updates cannot invalidate the archived
  settings fixture. Evidence: `.patcher-work/browser-lifecycle-retry-20261015.json`.
  This establishes process lifecycle behavior, not native visual acceptance.
- A subsequent live cycle also verified automatic discovery of the running
  browser's custom `--user-data-dir`, without supplying the profile override.
  Review/apply now retain that discovered root. Quoted paths and both flag forms
  are covered. Ambiguous multiple main-process sessions refuse before shutdown;
  selecting and restarting several independent user-data sessions remains a gap.
  Evidence: `.patcher-work/browser-profile-lifecycle-20261015.json`.
- All 12 file combinations passed again on a newly prepared original copy,
  followed by exact restoration. Trial preparation accepts an explicit reviewed
  build, checks actual launcher/DLL versions, and refuses source path escapes.
  Combination failures now enter recovery/restoration before returning.
- All eight advertised native-only selections also passed actual file
  transactions and final restoration with settings support absent. Inspection
  now advertises only reachable selections while retaining other hashes for
  recognition. Evidence: `.patcher-work/native-combinations-retry-20261015.json`.
- Read-only artifacts are refused before staging. Malformed signed adapters and
  conflicting catalog builds cannot replace the active verified cache. Publication
  requires fixed minimum runtime checks and matching tested source provenance.
- Alternate administrator credentials and native visual patch interaction tests,
  VM reset infrastructure, and an approved candidate-to-catalog publication remain
  separate acceptance tasks.
- Workflow definitions and signing tools exist locally; no workflows or release
  artifacts have been pushed or published.

## Second-machine desktop acceptance: 2026-10-15

A Windows 11 laptop completed the developer build's protected-installation
round trip using Aside 1.0.928.2 and a separate synthetic profile. The laptop's
desktop agent supplied screenshots and an interaction report; the development
machine independently retrieved the evidence and checked the final file hashes
and absence of a recovery journal. This was manual desktop acceptance, not the
automated VM publication gate.

- The unelevated GPUI app discovered the protected fixture and reviewed the full
  selection: Hide Ask Aside plus Bookmark folder appearance. The unavailable
  settings-dependent Chats control did not block those patches.
- Apply and Restore each received genuine UAC approval. Graceful browser restart
  retained the synthetic profile, and a separate manual restart retained folder
  colors, icons, and a nested folder rename.
- Screenshots confirm independent nested and identically named folder styles,
  the color palette/icon picker, and default menus and rendering after restore.
  The desktop agent also reported smooth folding of loaded inactive entries;
  static screenshots alone do not establish animation quality.
- Original and restored DLL SHA-256:
  `9cdc4b08d095abb48fdfec2babf7be54ed35eb67ec20af9341d4cf5302e5d510`.
  The two-patch output was
  `a58471d3915422cca397ed1a292969d6b1b34e41c01fed484d474cb9b410bef9`.
- The laptop's ordinary Aside installation remained unchanged. The test fixture
  was restored, the patcher exited, and the recovery journal was absent.
- Drag/drop, light theme, and the collapsed sidebar popup were not exercised.
  Alternate administrator credentials and automated VM reset/visual testing
  remain pending. This does not establish acceptance of every patch combination.

Local evidence is retained under
`.patcher-work/remote-transfer/evidence/` (report, six screenshots, and independent
state checks). These machine-specific artifacts are ignored by Git.

The copied developer executable initially failed because GPUI's debug Windows
renderer loaded shaders from its build-machine Cargo directory. The package-only
`gpui-pre-windows` development profile now disables debug assertions so that GPUI
embeds its compiled shaders. The rebuilt executable ran on the laptop without
Rust, the Windows SDK, or the developer's source paths. Repository discovery also
searches relative to the executable before using its build-time fallback.

The owner subsequently reported successful manual drag/drop (folder reorder,
cross-parent nested moves and bookmark moves). Light-theme inspection started but
was stopped before returning to dark; popup and remaining cases were not run.
Manual coordinate-driven acceptance is now replaced by a deterministic harness
workstream. See [Automated acceptance](AUTOMATED_ACCEPTANCE.md) for the native
protocol backend, runnable first scenario batch and explicit coverage gaps.

## Developer commands

```powershell
uv sync --extra catalog
cargo run --manifest-path desktop/Cargo.toml
uv run --extra catalog python -m unittest discover -q
cargo clippy --manifest-path desktop/Cargo.toml -- -D warnings
powershell.exe -NoProfile -File scripts/check.ps1
```

The JSON engine accepts one newline-terminated request on stdin and emits JSONL
progress followed by one result. Example read-only inspection:

```powershell
'{"protocol_version":1,"operation":"inspect"}' | uv run --extra catalog python -m aside_patcher.protocol
```

`preview` takes a complete `targets` list and an optional discovered `installation`
DLL path. `apply` takes the same selection plus its `preview_token`. `restore`
requires a preview of the empty selection. `recover` handles a pending journal;
`refresh` verifies the signed catalog. Errors contain a stable `code`, including
`shutdown_blocked` and `elevation_declined` for Retry/Cancel. Only the initiating
unelevated process coordinates shutdown and restart. The internal UAC helper
revalidates the mutation under the installation lock.

The following developer probes operate only on separately prepared fixtures:

```powershell
uv run --extra catalog python scripts/test_desktop_engine.py --trial <trial> --report <report.json>
uv run --extra catalog python scripts/test_browser_restart.py --trial <trial> --report <report.json>
uv run --extra catalog python scripts/test_uac_transport.py --expect canceled --report <report.json>
```

The optional UAC cancellation probe launches only an interpreter that exits; it
never performs installation. It is not required for the narrowed second-machine
smoke acceptance. Normal protected Apply requires genuine approval. Lifecycle reports intentionally keep `runtime_passed` false:
launch, hash, and file-transaction success do not establish visual behavior.

`prepare_desktop_trial.py --archive <archive> --version <version> --trial <new-directory>`
creates a native-only fixture without requiring the independently updated settings
component. Add `--settings <verified-original.js>` to exercise settings patches as
well. The combination runner tests only advertised states for the detected scope;
native-only restoration changes the DLL while leaving unsupported settings alone.

## Publishing setup still required

1. Connect a trusted interactive Windows runner labelled `Windows`, `X64`,
   `aside-ui-test`. Use isolated browser fixtures and synthetic profiles. A
   disposable VM remains preferable for unattended browser execution, but full
   visual regression or VM reset infrastructure is not a smoke-acceptance gate.
2. Enable repository variable `ASIDE_UI_TESTS_ENABLED=true` once that runner is
   ready. The workflow includes its own short smoke runner; a custom external
   `ASIDE_UI_TEST_SCRIPT` is no longer required. Unknown builds enter per-patch
   analysis without a prior supported-build registry entry.
3. Configure protected environment `compatibility-publishing` with the owner as
   reviewer and `ASIDE_SIGNING_KEY` matching the application's pinned public key.
   Keep private key material outside Git. Manual publishing dispatch remains
   the explicit approval fallback where environment reviewers are unavailable.
4. Inspect the candidate report and approve its immutable tested run ID. The
   publisher signs those exact bytes without rebuilding. It verifies all file
   transactions plus individual/interaction smoke checks and screenshot digests.
   A failing patch can be removed into a separately smoke-tested passing subset.

No workflow or release was published during local implementation. See
[CROSS_VERSION_SUPPORT.md](CROSS_VERSION_SUPPORT.md) for current results and
remaining native binding limitations.

## Quality status

Python unit tests, Black, Flake8, Pylint and docstring checks pass locally; Rust
Clippy passes with warnings denied. The aggregate quality script still exits
nonzero at Mollify: six existing churn hotspots plus new short-script/test
duplication and optional/development-dependency classification findings. These
findings are not waived or represented as a passing aggregate check.

## Second-machine acceptance scope (2026-10-15)

Per the user's revised scope, accept the installation smoke flow separately from
full UI regression coverage. Reuse the already verified protected Apply/UAC and
Restore/UAC evidence. The follow-up checks apply and expected hashes, successful
patched launch, settled screenshots, exact restoration, and original relaunch on
the isolated laptop fixture. The full routed-input matrix, popup and animation
measurements remain deferred. No broader runtime/publication flag is implied.

Local verification after the harness changes: 110 unit tests pass; Black and
Flake8 pass for the five native automation Python modules. Aggregate Mollify
status above remains unresolved.

The follow-up smoke run passed on the laptop. Both patched and restored browsers
launched; exact original DLL hashes were restored. Screenshots were reviewed:
folder decorations and hidden Ask Aside are present when patched, with standard
folders and Ask Aside restored afterward. See `second-machine-smoke.json` for
hashes and evidence digests. This does not claim the deferred UI matrix passed.
