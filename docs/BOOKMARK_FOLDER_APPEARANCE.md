# Bookmark folder appearance

The optional bookmark-folder-appearance patch targets Aside 1.0.928.2 and vendor
DLL SHA-256 9cdc4b08d095abb48fdfec2babf7be54ed35eb67ec20af9341d4cf5302e5d510.
Unknown inputs, changed hook instructions, and changed payload sources are refused.

## Behavior

Folder menus contain the eight group colors, Change icon, Reset appearance, New Folder, and
the original Rename, Delete, and Manage Bookmarks commands. Color and icon are
independent; subfolders have independent appearances without inheritance.
Policy-managed folders retain their original menus.

New Folder opens the native bookmark editor with the selected folder preselected
as parent. Saving creates an ordinary undecorated folder; cancel leaves bookmarks
unchanged. The native editor owns its normal selection and validation behavior.

The local profile preference aside.bookmark_folder_appearance stores preset
identifiers under bookmark GUIDs. Rename/move preserve decorations; subtree deletion
removes its decorations in one preference write. Invalid values use the standard
appearance. Disabling the patch retains preferences for re-enabling. Bookmark data
and the bookmark manager are unchanged.

## Native integration

The folder editor composes Aside's native palette/action controls and popup styling.
Change icon opens AsideIconPickerView on hover and uses its 48-icon collection.
Menus use the screen cursor converted through the native display/DIP API; keyboard
invocation falls back to the row. This is a folder-specific adapter. Native folder
commands still dispatch through their original handlers.

Rounded subtree backgrounds reuse native group colors, alpha, corner radius, and
the rounded Background painter. Nested hover, selection, and folder-drop fill and
outline use inset geometry. Expanded colored folders reserve 6 pixels below their
last visible descendant through native margins and recursive layout invalidation.
Shared nested endings accumulate padding; reset/collapse recalculate it. Standard
bookmark expansion remains; tab-group motion has not been transplanted.

Active-link focus outlines use the same native pill highlight generator as group
controls, with nested insets. Original generators are retained and restored when
customization no longer applies. Colored folder headers omit the tab hover fill.
The bookmark container also paints selected-row outlines independently at native
RVA 0x3893610. Its full-width pass is suppressed for decorated nested rows; their
row border pass reproduces the native half-pixel outline with nested bounds.
Hidden dragged rows are excluded from subtree backgrounds; row geometry/state
changes repaint the container, and dragged headers paint their own folder color.

Ten verified hooks cover owner construction/destruction, row icon updates/destruction,
folder menus, model removal, default background paint, and row background/border paint.
The embedded payload separates executable code and writable storage, with relocations,
unwind records, and CFG callback targets. It builds from immutable verified originals
and uses existing transactional backups/rollback. The Chats builder runs first;
static edits follow native builders.

## Verification

Trial artifacts and screenshots:
D:\Users\kubaj\programovani\re\tools\aside-analysis\bookmark-appearance.
The separate trial installation/profile contains synthetic bookmarks. External
chat-agent account content can still appear: profile isolation is not account isolation.

Live checks cover restart, rename/move, duplicate names, nested appearances, reset,
subtree deletion, same-profile window refresh, native commands, light/dark rendering,
hover submenus, cursor placement, folding stress, closing windows with menus open,
inset drag targets, active-link outlines after dragging, and native New Folder
save/cancel. Footer reset is checked by row geometry and screenshots.
Fresh combination checks must pass before their hashes are registered or installed.

The Python suite has 45 tests; the native storage/model harness uses MSVC /W4 /WX.
Black, Flake8, Pylint, and numpydoc pass. Mollify reports existing hotspots in
common/models.py, common/utils.py, pe.py, and recipes.py (99/100).

## Rebuilding

Compile package native_*.c files with MSVC into a separate object directory, then
run scripts/freeze_bookmark_payload.py on it. Inspect metadata before updating the
pinned manifest hash. Generate candidates from vendor originals and verify fresh
trial processes before updating output hashes. Compiler/hash checks alone do not
prove visual or interaction behavior.

The installed DLL was verified in a fresh normal Aside process, including folder
menus and hover icon selection. The updater had removed the original supported
settings bundle, so installation used the explicit --native-only mode. This mode
allows status and bookmark-appearance enable/disable, keeps all DLL hash guards,
and never reads or modifies settings files. The current settings asset hashes
were unchanged across installation. Other patch changes still require supported
settings artifacts. Native Chats and Ask Aside patches remain active; this does
not establish compatibility of their settings controls with a newer agent bundle.
