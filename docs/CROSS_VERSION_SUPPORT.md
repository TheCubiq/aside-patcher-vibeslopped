# Cross-version support

The desktop app and JSON engine now load hash-pinned bundled adapters as well as
approved signed catalog packages. Browser versions do not need a new hardcoded
registry entry before analysis can prepare a candidate.

## Current results

| Browser | Available patches | Evidence |
| --- | --- | --- |
| 1.0.914.1 | None | Read-only availability entry; instruction dependencies did not resolve. Group icons require a later browser. |
| 1.0.922.1 | Hide Ask Aside | Laptop apply, launch, primary effect, screenshot, exact restore and relaunch passed. |
| 1.0.928.1 | Hide Chats, Hide Ask Aside, both together | All four file transactions and all three individual/combined smoke selections passed on the laptop. |
| 1.0.928.2 | Existing reviewed patches retained | Existing adapters and original/output guards remain unchanged. Settings support still depends on the separately verified settings component. |

The table describes enabled bundled support. Bookmark-only native adapters for
`.922.1` and `.928.1` also passed laptop apply, launch, palette/color change,
exact restoration and relaunch. Their portable reports are retained alongside
the toolbar reports. Icon selection, persistence and new combined runtime
selections remain unverified; these adapters are not enabled in the bundled
client catalog yet. An unavailable bookmark adapter never blocks toolbar patches.

Native adapters now live in `compatibility/adapters/`, selected by exact original
DLL hash. Readable JSON records hook RVAs, native RVA mappings, source-template
hashes and the verified bookmark-only output hash. Separate generated payload
documents contain compiled patch code and relocation metadata, never vendor
binaries. The index pins the adapter; each adapter pins its payload document.
Changed templates, payloads, input hashes or reconstructed outputs are refused.
Line endings are fixed for portability across Windows checkouts.

The normal analyzer consumes these reviewed native adapters and prepares complete
data-only candidates, including nonconflicting toolbar combinations. Shared
rendering reproduces both laptop-tested bookmark outputs exactly. New candidates
still require their normal smoke gate before bundling or signing. Unknown builds
remain unresolved until their native dependencies are established; the private
cross-version discovery is now automated as described below, but remains a
candidate/evidence stage rather than an automatic adapter approval system.
Native settings-toggle adapters remain separate work. This is not a guarantee
that every patch supports every browser version.

## Adding support

Patch definitions declare an analyzer, optional `minimum_version` and
`maximum_version`, capability requirements, and a primary smoke-check description.
The existing `versions` list still describes bundled legacy CLI recipes; a range
is eligibility metadata, never proof that native instructions are compatible.

The instruction-site analyzer locates unique executable contexts, retains object
field/register operands, checks branch/data anchors, and relocates rewritten
branches. Ambiguous matches or changed dependencies produce per-patch reports.
Native payload analysis accepts exact reviewed bookmark adapters. Settings
analysis still requires its own adapter. Neither copies baseline addresses into
an unknown build.

## Unknown-build native discovery

Unknown bookmark builds now trigger read-only discovery during normal analysis.
It compares complete x64 function bodies and unwind boundaries while retaining
registers, object-field offsets and local branch topology. Unique references from
matched callers can disambiguate otherwise identical functions. RIP displacement
masking explicitly handles prefixed SIMD instructions whose displacement size is
misreported by Capstone.

Typed resource checks compare full vtable regions, vector names and path words,
all 48 icon keys/labels, preset colors, PE-defined TLS identity and referenced
constants/global slots. Baseline dependency identities come from reviewed data;
target addresses are discovered rather than copied from a version map. Repeated
method checks are cached. Analysis has a 120-second default budget and preserves
partial evidence on expiry.

```powershell
uv run --extra catalog python scripts/native_discovery.py --archive <official-archive> --version <browser-version> --output <fresh-report.json>
```

Reports contain candidate RVAs, per-dependency evidence/failures and exact image
hashes. They are always non-installable: discovery alone cannot authorize an
adapter. Unresolved dependencies, payload rebuilding and runtime smoke validation
remain explicit gates before publishing. The earlier private resource-mapping
scripts are not required to run this discovery command.

The `.928.1` rediscovery resolved 104 of 106 dependencies in 38 seconds, without
using precomputed target addresses. Resolved addresses matched the reviewed map.
Two bindings in one vtable region remained blocked: its method at baseline RVA
`0x1a06380` has unmasked byte changes near offsets `0x122a`–`0x1230`, after a
`UD1` instruction. Linear decoding differs there. The report correctly requires
independent instruction/data validation instead of broadly masking those bytes.
Both builds' existing reviewed adapters remain usable; this is a limitation of
automatic rediscovery, not evidence that the patch fails on `.928.1`.

```powershell
uv run --extra catalog python scripts/compatibility_release.py analyze --archive <official-archive> --version <browser-version> --output <fresh-candidate>
```

Inputs must come from a unique verified official archive. Reports identify exact
input, baseline, analyzer and patch-definition hashes. An unchanged completed
analysis can be reused; changed inputs require a fresh output directory.
Candidates remain private and unapproved until runtime evidence passes.

## Short smoke validation

Prepare a new confined trial with `prepare_desktop_trial.py --candidate`, then:

```powershell
uv run --extra catalog --extra automation python scripts/smoke_candidate.py --allow-unsigned-candidate --candidate <candidate> --trial <isolated-trial> --report <evidence>/runtime.json
```

The unsigned flag exists only on this developer fixture runner. The desktop app
does not accept unsigned downloaded packages. The fixture runner validates actual
browser versions, uses the production recipe/transaction/parser code, and refuses
paths outside its trial. It never patches the ordinary browser installation.

Every advertised combination gets a file transaction check. Runtime checks cover
individual patches plus interaction sets sharing an artifact, rather than every
mathematical combination. Each checks launch, primary effect, settled screenshots,
exact restoration and original relaunch. A fresh synthetic profile isolates each
selection. The runner opens the built-in bookmark manager, avoiding new-tab-page
rules that normally hide toolbar controls. Shutdown uses graceful CDP browser
quit followed by process checks; blocked shutdown never triggers forced killing.

Layout settling is bounded, with a short fallback delay. A smoke case exceeding
five minutes fails after safe cleanup. `--resume` rechecks current originals and
reuses completed exact-candidate transactions and passing screenshot-bound cases.
Failed cases remain pending. Screenshots and machine-specific logs stay outside
Git; reports and bundled package digests are portable.

Failed patches do not prevent testing independent selections. The `subset`
command removes failed patches/interactions from a safely restored candidate and
requires a fresh smoke report for the reduced package. The publication gate
checks the candidate digest, all transactions, required runtime selections,
restoration and screenshot digests. It does not require theme, animation or drag
regression suites.

## Distribution

The manually triggered workflow analyzes unknown official builds and can test a passing
subset. A trusted interactive Windows runner and owner signing credentials still
need repository setup. One-click approval publishes the exact tested bytes;
clients retain exact-hash guards and verified offline support. No release was
published during this implementation.

The `bundle` command adds smoke-tested data to the source distribution, without
publishing it. Bundled packages use the same declarative parser as signed packages.
Read-only availability entries advertise no enableable patches. Existing bundled
adapters cannot be silently overwritten with different outputs.

Third-party patch repositories/imports are a future feature. This work establishes
patch-local metadata and independent results, not a community distribution system.

## Verification status

135 local unit tests pass after native discovery integration. Existing aggregate
quality findings below remain separate from targeted adapter checks.
The last aggregate quality run failed Mollify (88/100, 50 findings), including
existing churn and new orchestration complexity/duplication findings; it is not
reported as fully green. Laptop fixtures were restored to exact vendor hashes,
closed, and temporary smoke tasks removed. The interrupted baseline recheck does
not qualify as a new runtime approval.
