# Rigify Export

Rigify Export is bundled in Script Toolkit. Choose **Rigify Export** from the
Script Toolkit tool dropdown to create an editable, temporary deform-bone
preview for a Rigify armature. The original rig stays unchanged. Profiles store
the final bone names and hierarchy. Action Bakery and FBX export remain in the
Item tab, as in the standalone add-on. Saved profiles can also embed the
exporter for use in blend files when Script Toolkit is not enabled.

The hierarchy options follow Game Rig Tools 4.3.0 by TinkerBoi; this extension
implements the workflow independently and is distributed under GPL-3.0-or-later.

## Workflow

1. Open **View3D → Sidebar → Script Toolkit** and choose **Rigify Export** from
   the tool dropdown. Select an existing profile from
   the dropdown, or click the standalone **New** button below it. Enter a name
   in the popup and confirm to open the profile setup. **Refresh** reloads
   profiles saved in the embedded exporter script; the trash button deletes the
   selected profile.
2. In the profile setup, choose the Rigify armature in **Control Rig**. The
   profile is assigned to that rig automatically, and the **Game Rig** field
   starts empty. Choose **Hierarchy Settings** and set **Disconnect Bones**.
3. Click **Gen Game Rig**. The add-on creates a visible, deform-only copy and puts
   it in the **Game Rig** field. Enter Edit Mode on it and delete any DEF bones
   that should not be exported. If you change **Hierarchy Settings** or
   **Disconnect Bones**, click **ReGen Game Rig** to rebuild it from the current
   settings.
   The preview's matching bones copy location and rotation from the Control Rig
   to make pose inspection easier; these preview constraints are discarded when
   the profile is added to the export UI and are never used for FBX baking.
4. In **Meshes to Export**, add selected meshes or all meshes bound to the rig,
   then leave checked only the meshes that belong in this profile's FBX.
5. Click **Save to UI**. The add-on saves the remaining bones and their hierarchy
   to the profile, embeds the exporter script and profile data in the `.blend`,
   then removes the temporary Game Rig. Generate it again and click **Save to UI**
   whenever the bone set needs editing. Action and Mesh settings are refreshed
   in the embedded data when you save the file.
6. In **View3D → Sidebar → Item → Rigify Export**, choose a profile from the
   **Export Profile** dropdown. In Action Bakery, use **+** beside the blank list
   to choose one or more Actions in its searchable checkbox popup. The list
   shows only added Actions and provides per-Action
   enable, remove, and reorder controls. Select an Action to configure **Set Baked Action name** and
   **Frame Range** (**Action**, **Set**, or **Trim**), choose the output folder and
   whether to include the meshes selected in the profile, then click **Export FBX**.
   If no Actions have been added, the panel says **Action list is empty** and
   keeps the Action picker available. When opening the file without the add-on, the
   embedded exporter registers on file load if Blender permits trusted scripts to
   run. If Auto Run is disabled, run the internal text block
   **REX_Standalone_Exporter.py** from Blender's Text Editor.

Exports are Unity-oriented FBX files. Animation uses Blender's
`anim_utils.bake_action_objects` visual pose bake, the bake entry point used by
Game Rig Tools' Action Bakery. Each Action is exported with its selected frame
range, and the temporary export rig and mesh copies are removed afterwards.

## Install

This feature is distributed as part of Script Toolkit. Blender's built-in
Rigify and FBX exporter are required.

Only allow auto-running scripts from blend files you trust. Blender disables
automatic script execution by default for security; the embedded text block can
also be run manually from the Text Editor.
