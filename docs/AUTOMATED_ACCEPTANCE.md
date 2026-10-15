# Automated desktop acceptance

## Acceptance scope

Second-machine acceptance is an installation smoke check: detect the supported
build, apply a selected combination through genuine UAC, verify hashes, launch
without errors, capture the active patches, restore exact originals, and relaunch.
Existing protected-installation/UAC evidence is reused; it need not be repeated
for each UI harness change. Capture after a short settling delay so startup
animations do not produce misleading evidence.

The broader regression harness below is optional follow-up work. It is not a
prerequisite for accepting that smoke check. Smoke acceptance does not authorize
publishing untested build adapters or claim complete visual regression coverage.

## Optional regression harness

The native driver uses Chromium **UI DevTools**, a separate endpoint from page
CDP. It reads the Views tree and metadata, resolves drawn controls by class and
label, then derives pointer positions from native bounds and DPI metadata. Integration
runs route real Windows input only to the fixture's own windows. Focused tests
can dispatch native callbacks directly. Neither uses screenshots as selectors,
manual coordinate chains, or an AI operator.

Chromium documents native inspection at
<https://chromium.googlesource.com/chromium/src/+/main/docs/ui/ui_devtools/index.md>.
Input dispatch additionally requires the explicitly enabled test feature
`ui-debug-tools-enable-synthetic-events`. Both endpoints are loopback-only.
The runner checks the content endpoint's command line against its fresh synthetic
profile. Existing listeners, running fixture processes and patched starting
images are refused.

## Run

Prepare a separate trial with `scripts/prepare_desktop_trial.py`; never point this
at the everyday installation. The trial must have `Application/`, `profile/`, and
`request-context.json`, with the executable, DLL and profile confined inside it.
Run from an interactive Windows desktop as its normal user:

```powershell
uv sync --extra catalog --extra automation
uv run --extra catalog --extra automation python scripts/run_native_acceptance.py `
  --trial C:\AsideTest\trial `
  --suite tests/fixtures/native-1.0.928.2.json `
  --bookmarks tests/fixtures/automation-bookmarks.json `
  --report .patcher-work/native-acceptance/report.json `
  --routed-input
```

Each run creates a new profile, seeds synthetic bookmarks, applies the reviewed
selection, launches the isolated browser, runs assertions, closes it gracefully,
and restores the exact original hashes. A protected trial still requires real UAC
approval; a per-user trial allows unattended regression runs. This does not
automate or bypass the secure desktop approval itself.

Evidence includes per-case native trees, a bounded protocol transcript, precise
failure messages, the suite digest and original hashes. An action's successful
dispatch is not a passing assertion. Visibility, menu counts and native color
metadata are checked as postconditions. Widget destruction on mouse-down is
handled explicitly; other protocol errors remain failures. Ambiguous selectors
are refused. Indexed palette selection requires the reviewed control count.

## Current coverage and limits

The first versioned suite covers unloaded folder expand/collapse, native folder
menu entries, eight palette controls, an actual color change, hover opening the
48-icon picker, choosing an icon, reset restoring the original label color, and
folding a loaded inactive bookmark after a second tab is activated through CDP.
It also selects Dark, Light, then Dark in the real Chromium settings dropdown
through CDP/shadow DOM and checks native label colors change and return. It does
not edit preferences on disk or assume that page color-scheme emulation changes
the browser theme.

Earlier focused runs passed 17 core scenarios and restart persistence, including
identity changes and independent nested/duplicate-name decorations. Real pointer
menu routing and drag movement were also checked separately. These results do
not establish that the complete routed-input suite passes: its latest run stopped
when Windows denied foreground input. Full combination runs, popup retention,
multi-window refresh, visual clipping and animation measurements remain deferred.
The real-input runner requires an unlocked interactive desktop.

Reports deliberately retain `runtime_passed: false`. They are not accepted as
publication evidence by `compatibility_release.py`. The CI publication gate must
remain closed until the full advertised combinations and required behavior
matrix have automated, independently verified coverage. The earlier manual
laptop report remains separate evidence.
