# Reverse-engineering notes

## Target identity

- Aside `1.0.928.2`
- Input: `C:\Program Files\Aside\Application\1.0.928.2\Aside.dll`
- SHA-256: `9CDC4B08D095ABB48FDFEC2BABF7BE54ED35EB67EC20AF9341D4CF5302E5D510`
- The analysis copy and Ghidra project live under `D:\Users\kubaj\programovani\re\tools\aside-analysis`, outside this repository.

## Findings

- The application is a native Chromium/WinUI build. It has no `.asar` archive;
  the relevant code is in `Aside.dll`.
- The DLL contains the native vertical-tab-strip identifiers
  `AsideSectionRowButton` and `AsideNewChatTabButton`, along with the preference
  `aside.vertical_tabs.chats_section_expanded`. The latter controls expansion;
  analysis has not shown a corresponding Chats visibility preference.
- The installed `AsideAgentManager\assets\appearance-DT709D6U.js` confirms the
  settings UI intentionally keeps Chats checked and disabled, with the tooltip
  “Chats always stay in the sidebar.” The only section toggle exposed there is
  `verticalTabsBookmarksSectionEnabled`.
- Targeted decompilation of the native vertical-tab-strip setup around
  `0x1836a51c0` found it reading `chats_section_expanded`; the nearby callback at
  `0x1836afe60` writes that same preference. This confirms a collapse/expand
  setting, not a hide/show switch. The known preference set also includes
  `bookmarks_section_enabled`, but no `chats_section_enabled`.
- The `Chats`/`Chats with Aside` and `New Chat` string references lead into
  native task/string construction. `Archive all chats` and `Show all chats`
  references lead into native section menu code. None of these references alone
  establishes a safe way to suppress the section.
- `FUN_180e9ccd0` updates the native Ask Aside toolbar button. A breakpoint on
  `View::SetVisible`, filtered by native control ID `1009`, confirmed the call
  from this function when navigating to Settings.
- Ghidra imported the exact binary and completed its import. Its full
  entry-point analysis exceeded the configured 600-second limit; a saved
  database was then used for targeted disassembly and decompilation. RE-MCP's
  worker did not reach a usable MCP endpoint in this environment.

## Patch decision

### Chats, verified in memory

- Correct runtime mapping is `loaded Aside base + (static address - 0x180000000)`.
  At loaded base `0x7ff8a5770000`, constructor `0x1836a51c0` is
  `0x7ff8a8e151c0`. Earlier manually calculated addresses were off by `0x5000`.
  Corrected live disassembly matches the saved Ghidra output.
- Constructor assigns the Chats section container to `this + 0x5f0`
  (`param_1[0xbe]`). The Bookmarks container is separate at `this + 0x5c8`.
- `FUN_1836b6b00` calculates visibility, compares it against the Chats
  container's byte at `+0x1d8`, then dispatches virtual method `+0x90`.
- RVA `0x36b6b48`, file offset `57368904`: original
  `0f84df000000` (conditional jump to `0x1836b6c2d`), proposed
  `e9e000000090` (unconditional jump to the same address plus NOP).
  Destination sets `edx = 0` before the Chats visibility comparison;
  the later Bookmarks calculation and the original `dil` value are preserved.
- The first one-site trial did not keep Chats hidden. A hardware watchpoint on
  the container's visibility byte proved a later update restored visibility.
  Its caller is `FUN_1836d1e20`, reached during sidebar initialization/update.
- Second site: RVA `0x36d27f9`, file offset `57482745`, replaces `89da`
  (`mov edx,ebx`) with `31d2` (`xor edx,edx`) at this container's visibility call.
- With both sites changed in memory, new test windows showed Bookmarks followed
  immediately by Tabs, with the Chats header and New Chat row absent. Google
  and Appearance Settings worked. Native UI Automation confirmed no Chats/New
  Chat controls in the test window while Bookmarks/Tabs were present.

### Ask Aside, verified in memory

- Constructor `FUN_183520110` assigns control ID `0x3f1` at View offset `0x180`.
- A conditional breakpoint on `FUN_185190ec0` (View::SetVisible), filtered by
  this ID, hit from `FUN_180e9ccd0` at return RVA `0xe9cf68`. This independently
  ties the task/string update function to the actual visible toolbar control.
- RVA `0xe9cf50`, offset `15320400`: replace `89ea80f201` (visibility derived
  from `ebp`) with `31d2909090` (false plus NOPs).
- RVA `0xe9cfa4`, offset `15320484`: replace `b201` (true) with `31d2` (false)
  in the alternate show path. Existing hide paths remain intact.
- With both edits in memory, Ask Aside was absent on Google, where it had been
  visible before the trial. Native UI Automation also found no Ask Aside control.

### Manifest and copy validation

- Signatures include unchanged instruction context to require a unique match.
- Both targets are separately selectable; their original, individual, and
  combined SHA-256 states are explicit in `aside_patcher/builds.json`.
- Copies `Aside.chats.dll`, `Aside.ask-aside.dll`, and `Aside.both.dll` outside
  Git passed independent/sequential application, idempotence, and exact restore.
- Original installed DLL and read-only hash-keyed backup were reverified.
  These runtime trials changed memory only; fresh installed launch verification
  is recorded separately after applying.

### Installed verification — 2026-10-10

- Applied both targets with administrator privileges. Installed SHA-256:
  `c54cad6bdf4f977bacdaa2ad66f7fbd6d13cebed2c0e6b2a361c9a213a446815`.
- Reverified the immutable original backup: its SHA-256 matches the supported
  original and its ReadOnly attribute is set.
- Relaunched Aside from disk without a debugger attached (browser PID 2328).
  On Google, the Chats section, New Chat row, and Ask Aside toolbar button were
  absent; Bookmarks and Tabs remained visible. Native UI Automation confirmed
  the same controls. Appearance Settings rendered and its sidebar menu worked.
- Screenshot evidence is outside Git at
  `D:\Users\kubaj\programovani\re\tools\aside-analysis\installed-google.png`.
- All 12 unit tests passed. Copied DLLs also passed byte-for-byte restoration;
  the installed DLL was left patched after successful launch/UI verification.
- Atomic replacement uses a single exclusive temporary-file creation attempt.
  A regression test confirms permission denial fails immediately and preserves
  the installed file.
