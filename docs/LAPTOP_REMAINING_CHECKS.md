# Remaining laptop desktop checks

Continue the Aside Patcher acceptance test on the laptop. Use only the protected
test fixture and synthetic profile below. The previous cycle restored the original
DLL, closed the patcher and test browser, and removed its temporary launch tasks.
SSH remains available. Do not change the ordinary Aside installation.

- Workspace: `<test-workspace>`
- GUI: `desktop\aside-patcher-desktop.exe` within that workspace.
- Test browser: `<protected-trial>\Application\Aside.exe`
- Profile: `acceptance-profile` within the workspace.
- Launch context and arguments: inspect the existing `launch-acceptance.ps1` and
  `acceptance-context.json`. Launch the GUI as the normal desktop user. Never
  switch to the ordinary browser or its profile if fixture launch fails.

## Test sequence

1. Open the fixture with its synthetic profile and launch the unelevated patcher.
   Select **Hide Ask Aside** and **Bookmark folder appearance** only. Review the
   exact fixture path. Apply and let the owner approve the genuine UAC prompt.
   No cancellation probes or automatic UAC interaction.
2. Test folder and bookmark drag/drop: reorder colored folders; move a colored
   nested folder between parents; move a bookmark into and out of a colored
   folder. Confirm decoration follows folder identity, spacing stays consistent,
   and drag outlines/backgrounds respect nesting. Use synthetic data freely.
3. Switch Aside to light theme using its own settings. Verify palette, icons,
   nested backgrounds, selected/hovered entries, folding and drag outlines.
   Return to dark theme afterward. Record any unavailable setting as untested.
4. Collapse the sidebar, open its popup, then open a bookmark folder context menu
   and hover **Change icon**. Confirm popup and submenu remain open for interaction.
   Select an icon and confirm the result. Repeat with a nested folder.
5. Reset one folder's appearance. Confirm its child keeps independent styling.
   Delete a synthetic decorated folder and create a replacement with the same
   name; confirm the replacement starts with default appearance.
6. Restore originals through the patcher with genuine owner-approved UAC.
   Confirm the restored DLL hash is
   `9cdc4b08d095abb48fdfec2babf7be54ed35eb67ec20af9341d4cf5302e5d510`.
   Verify default folder rendering and Ask Aside return. Close the GUI and test
   browser gracefully; never force termination.

Save `remaining-desktop-acceptance-report.md` and clearly named screenshots in the
workspace. Use recordings for animation claims where supported. Report each case
as PASS, FAIL, or UNTESTED, with exact reproduction steps for failures. Keep hash,
launch and visual checks separate. This report is not an automated publication
gate and must not mark all combinations or runtime publication acceptance passed.
