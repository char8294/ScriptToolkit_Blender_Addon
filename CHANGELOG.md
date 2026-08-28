# Script Toolkit Changelog

## v0.6.12

- Removed Action-list synchronization from the Panel draw callback to avoid Blender 5.2's read-only UI draw context error.
- Kept Action-list refresh in load, Generate, registration, and the explicit Refresh operator paths.

## v0.6.11

- Preserved the generated Rig's Active Action and NLA tracks/strips when the Rigify Quaternion setup regenerates the rig.
- Refreshed the Quaternion Action list automatically after Rigify Generate and when opening a Blender file.

## v0.6.10

- Added a top-level Rigify setup button to set `Meta_Armature_2` bones to Quaternion except both clavicles, then regenerate `RIG-Meta_Armature_2` using the existing Rigify target.

## v0.6.9

- Moved the Quaternion converter start control to the top of its panel and labeled it `Run`.

## v0.6.8

- Added Animation to Quaternion for `RIG-Meta_Armature_2`, with Active Action mode and a selectable Action list.
- Added optional overwrite mode to replace the source Action in place without creating a `_QUAT` copy.
- Conversion samples only existing rotation keyframe times and leaves the two clavicle controls as XYZ.

## v0.5.0

- Added Turntable Camera, Quick Render, and Learn Node Blender to the top-level Tool dropdown.
- Embedded Turntable Camera and Quick Render UI into the selected Script Toolkit Tool view and removed their standalone panels from the combined package.
- Kept Learn Node's Geometry Node Editor panel and HUD running continuously, with a dropdown note pointing users to the Node page.
- Removed the individual GitHub update features from the bundled tools while retaining the Script Toolkit package updater.
- Bundled Learn Node JSON data and updated release packaging for the new modules and data files.

## v0.4.10

- Updated Create Root Motion Shape to offer CUBE, ICO_SPHERE, red CYLINDER, and blue CYLINDER presets for multiple selected Pose bones.
- Replaced the custom Root Motion bake/inversion workflow with Blender's native Bake Action dialog.
- Added Bone Constraint pairing that uses the first matching `RM_<Bone>` Object, including Blender numeric suffixes such as `.001` and `.002`.

## v0.4.9

- Added Create Root Motion with per-bone helper shape creation for Root, Pelvis, Foot, Foot Front, and Foot Back bones.
- Added Root Motion animation baking that keys the selected helper object, removes its Root Motion constraints, and makes the selected bone copy the baked object's location and rotation while preserving the bone's existing Action.

## v0.4.8

- Added a 4-Legged preset to IK Helper Bones with separate Front/Back names for Pole, MCH-IK, and Foot helpers.
- The 4-Legged preset now creates the Front Pole along global +Y and the Back Pole along global -Y.
- Added IK Target and Pole Target actions for creating Chain Length 2 IK constraints and applying a Pole Target to all IK constraints on the active bone without muting the Pole bone's Damped Track.

## v0.4.7

- Removed Target selector from Batch Rename; Batch Rename now strictly operates on Armature Bones.
- Clarified the Prefix section to explicitly indicate it applies to Vertex Groups ("Vertex Group Prefix").

## v0.4.6

- Added Preset dropdown (2-Legged vs 4-Legged) to Batch Rename section in Biped Names Helper.
- Added preset rules for 2-Legged (`" R jts"` -> `""`, `".R"` and `" L jts"` -> `""`, `".L"`).
- Added preset rules for 4-Legged (`" RF jts"` / `" LF jts"` -> `" Front"`, `".R"` / `".L"` and `" RB jts"` / `" LB jts"` -> `" Back"`, `".R"` / `".L"`).

## v0.4.5

- Added preset dropdown (2-Legged vs 4-Legged) to Biped Names Helper's Set Bone Name section.
- Added 2-Legged preset support with Pole (`POLE-IK_LEG.L`), MCH-IK (`MCH-IK_LEG.L`), and Foot (`FOOT_LEG.L`) bone renaming.
- Empty to Bone can now create one bone from each selected Armature origin/pivot, in addition to selected Empties.

## v0.4.4

- Grouped Setup Symmetry Names and Restore Original Names under Biped Symmetry Names box in Biped Names Helper.
- Added a Quick Set Bone Name section with Front / Back labels, textboxes, and direct rename buttons ("Rename") for `POLE-IK_LEG`, `MCH-IK_LEG`, and `FOOT_LEG` bones (.L / .R).
- Added a Clear button to the Generate Preview section in Biped Names Helper.

## v0.4.3

- Added Delete Bones by Name feature to Align Bones tool with a popup report dialog.

## v0.4.2

- Updated Align Bones tool to operate directly on the active armature and always display armature controls.
- Added Armature display options (Show Names, Show Axes, Axes Position) to Biped Names Helper.
- Fixed KJ Export temporary duplicate meshes being exported with a `.001` suffix.

## v0.4.1

- Added a KJ Export option to remove bones without skin weights separately for each exported mesh.
- Preserved every weighted bone's complete parent chain and kept all pruning on temporary export copies.

## v0.4.0

- Integrated KJ Export into the Script Toolkit tool selector.
- Grouped all feature modules, including KJ Export, under the bundled `features/` package.
- Updated release packaging so feature submodules are included in the install ZIP.

## v0.3.15

- Replaced the Target Bone rename dialog with inline editing in the active mapping row.
- Added Source and Target pickup buttons that assign the active selected armature.

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
