# Script Toolkit Changelog

## v0.3.14

- Made mapping-list labels visibly left-aligned inside their full-width clickable cells, with right-side ellipsis for long names.

## v0.3.13

- Left-aligned Source Bone and Target Bone text in mapping-list rows.

## v0.3.12

- Fixed Alt-click deselection activating the row being removed instead of preserving the previous active row.

## v0.3.11

- Added Ctrl-click to extend mapping-row selection and Alt-click to remove individual rows from it.
- Added Update Bone List to merge changed armatures while preserving compatible mappings, retarget settings, and selection.

## v0.3.10

- Made Target Bone cells selectable like Auto-Rig Pro and editable by double-clicking them.
- Added separate Rename Source to Target and Rename Target operations with shared Find, Replace, Prefix, and Suffix fields.
- Removed the selection help text below the mapping controls.

## v0.3.9

- Changed ARP mapping rows to native-style flat list entries: a normal click selects one row, while Shift-click selects an inclusive range from the previous row.
- Fixed Import/Export file selectors returning `None` from `invoke()` under Blender 5.1.

## v0.3.8

- Changed ARP Retarget Preset multi-selection from checkboxes to clickable highlighted rows with Shift-click range selection.
- Added complete Source/Target armature and mapping reversal, plus ARP-style Left-to-Right / Right-to-Left bone-list mirroring.
- Build Bone List now includes every source and target data bone, with visible list/armature counts and a searchable Target Bone field.
- Removed the separate Rename Target button; the active Target Bone field remains directly editable.

## v0.3.7

- Fixed ARP Retarget Preset bone rows being hidden by the custom UIList filter.

## v0.3.6

- Added ARP Retarget Preset for building multi-select source/target bone mappings.
- Added Find/Replace rename, target clearing, manual target rename, `.bmap` import and Auto-Rig Pro-compatible export.

## v0.3.0

- Added GitHub update checker and one-click updater.
- Added a scrollable Hair Check sequence list with live progress.
- Improved Hair Check sequencing, visibility restoration, and timer cleanup.

## v0.2.0

- Added Check Hair And Cap and Biped Names Helper to the Script Toolkit dropdown.
- Added isolated Blender workers for batch FBX tools.
