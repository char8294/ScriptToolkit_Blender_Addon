"""Blender integration test for the bundled Rigify Export feature."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy


def _load_addon():
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "script_toolkit",
        repo_root / "__init__.py",
        submodule_search_locations=[str(repo_root)],
    )
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    spec.loader.exec_module(addon)
    return addon


class _RecordingLayout:
    def __init__(self):
        self.box_calls = 0
        self.labels = []

    def box(self):
        self.box_calls += 1
        return self

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def label(self, **kwargs):
        self.labels.append(kwargs.get("text", ""))

    def prop(self, *_args, **_kwargs):
        return None

    def operator(self, *_args, **_kwargs):
        return SimpleNamespace()

    def separator(self, *_args, **_kwargs):
        return None

    def template_list(self, *_args, **_kwargs):
        return None


def _new_rig(name):
    armature = bpy.data.armatures.new(name + "Data")
    armature["rig_id"] = "test-rigify-id"
    rig = bpy.data.objects.new(name, armature)
    bpy.context.scene.collection.objects.link(rig)
    bpy.ops.object.select_all(action="DESELECT")
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    bone = armature.edit_bones.new("DEF-root")
    bone.head = (0.0, 0.0, 0.0)
    bone.tail = (0.0, 0.1, 0.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    return rig


def _new_profile(name):
    result = bpy.ops.rex.new_profile(profile_name=name)
    assert "FINISHED" in result
    scene = bpy.context.scene
    return scene.rex_draft_profiles[-1]


def run():
    addon = _load_addon()
    addon.register()
    scene = bpy.context.scene
    scene.script_toolkit.tool = "RIGIFY_EXPORT"
    assert isinstance(addon.rigify_export.core.better_fbx_available(), bool)
    assert "Tracks" in addon.rigify_export.core._better_fbx_animation_types()

    # The Rigify entry skips the generic description box circled in the user's
    # screenshot while keeping the original profile panel contents.
    layout = _RecordingLayout()
    addon.ST_PT_panel.draw(SimpleNamespace(layout=layout), bpy.context)
    assert layout.box_calls == 0
    assert not any(label.startswith("Preview Rigify") for label in layout.labels)

    rig_a = _new_rig("RigA")
    rig_b = _new_rig("RigB")

    profile = _new_profile("RigSwitch")
    uid = profile.uid
    profile.control_rig = rig_a
    owner, profile = addon.rigify_export.ui._profile_for_uid(uid)
    assert owner == rig_a and profile.control_rig == rig_a
    assert addon.rigify_export.ui._control_rig_editable(profile)

    # Correcting the Control Rig moves the profile while retaining its UID and
    # selection. Clearing the field returns it to the draft list.
    profile.control_rig = rig_b
    owner, profile = addon.rigify_export.ui._profile_for_uid(uid)
    assert owner == rig_b and profile.control_rig == rig_b
    assert len(rig_a.rex_profiles) == 0
    assert scene.rex_main_profile_uid == uid

    profile.control_rig = None
    owner, profile = addon.rigify_export.ui._profile_for_uid(uid)
    assert owner is None and profile.control_rig is None
    assert scene.rex_main_profile_uid == uid

    profile.control_rig = rig_a
    owner, profile = addon.rigify_export.ui._profile_for_uid(uid)
    assert owner == rig_a and profile.control_rig == rig_a

    preview_profile = _new_profile("PreviewLock")
    preview_uid = preview_profile.uid
    preview_profile.control_rig = rig_a
    owner, preview_profile = addon.rigify_export.ui._profile_for_uid(preview_uid)
    assert owner == rig_a and addon.rigify_export.ui._control_rig_editable(preview_profile)
    scene.rex_control_rig = rig_a
    scene.rex_main_profile_uid = preview_uid
    bpy.ops.object.select_all(action="DESELECT")
    rig_a.select_set(True)
    bpy.context.view_layer.objects.active = rig_a
    result = bpy.ops.rex.generate_preview(profile_uid=preview_uid)
    assert "FINISHED" in result
    owner, preview_profile = addon.rigify_export.ui._profile_for_uid(preview_uid)
    assert preview_profile.preview_object is not None
    assert not addon.rigify_export.ui._control_rig_editable(preview_profile)
    addon.rigify_export.core.remove_preview_object(preview_profile)
    scene.rex_main_profile_uid = uid
    preview_index = next(
        index for index, candidate in enumerate(rig_a.rex_profiles)
        if candidate.uid == preview_uid
    )
    rig_a.rex_profiles.remove(preview_index)
    rig_a.rex_profile_dropdown = uid

    # Saving a later profile must preserve the main Export Profile menu order,
    # its selected item, and the Item-tab Action Export selection.
    profile.configuration_valid = True
    profile.source_signature = addon.rigify_export.core.hierarchy_signature(
        rig_a, profile.hierarchy_mode, profile.disconnect_bones
    )
    first_bone = profile.export_bones.add()
    first_bone.name = "DEF-root"

    save_profile = _new_profile("SaveTarget")
    save_uid = save_profile.uid
    save_profile.control_rig = rig_a
    owner, save_profile = addon.rigify_export.ui._profile_for_uid(save_uid)
    assert owner == rig_a
    save_profile.configuration_valid = True
    save_profile.source_signature = addon.rigify_export.core.hierarchy_signature(
        rig_a, save_profile.hierarchy_mode, save_profile.disconnect_bones
    )
    bone = save_profile.export_bones.add()
    bone.name = "DEF-root"

    scene.rex_control_rig = rig_a
    scene.rex_main_profile_uid = save_uid
    rig_a.rex_profile_dropdown = save_uid
    before_items = [
        (item[0], item[1])
        for item in addon.rigify_export.properties._main_profile_items(scene, bpy.context)
    ]
    assert [item[0] for item in before_items] == [uid, save_uid]

    bpy.ops.object.select_all(action="DESELECT")
    rig_a.select_set(True)
    bpy.context.view_layer.objects.active = rig_a
    result = bpy.ops.rex.save_profile(profile_uid=save_uid)
    assert "FINISHED" in result

    after_items = [
        (item[0], item[1])
        for item in addon.rigify_export.properties._main_profile_items(scene, bpy.context)
    ]
    assert after_items == before_items
    assert scene.rex_main_profile_uid == save_uid
    assert rig_a.rex_profile_dropdown == save_uid
    assert any(
        entry["uid"] == save_uid
        for entry in addon.rigify_export.standalone.embedded_profile_entries()
    )
    state_text = bpy.data.texts.get(addon.rigify_export.standalone.STATE_TEXT)
    saved_state_before_rig_switch = state_text.as_string()
    owner, save_profile = addon.rigify_export.ui._profile_for_uid(save_uid)
    assert addon.rigify_export.ui._control_rig_editable(save_profile)
    save_profile.control_rig = rig_b
    owner, save_profile = addon.rigify_export.ui._profile_for_uid(save_uid)
    assert owner == rig_b and not save_profile.configuration_valid
    assert scene.rex_main_profile_uid == save_uid
    assert not any(
        entry["uid"] == save_uid
        for entry in addon.rigify_export.standalone.embedded_profile_entries()
    )
    assert any(
        entry["uid"] == uid
        for entry in addon.rigify_export.standalone.embedded_profile_entries()
    )
    current_state = state_text.as_string()
    state_text.clear()
    state_text.write(saved_state_before_rig_switch)
    restored = addon.rigify_export.standalone.restore_embedded_state()
    assert restored == 0
    assert len(rig_a.rex_profiles) == 1
    assert len(rig_b.rex_profiles) == 1
    state_text.clear()
    state_text.write(current_state)
    result = bpy.ops.rex.refresh_profiles()
    assert "FINISHED" in result
    assert scene.rex_main_profile_uid == save_uid
    assert addon.rigify_export.ui._profile_for_uid(save_uid)[0] == rig_b

    bpy.ops.object.select_all(action="DESELECT")
    rig_b.select_set(True)
    bpy.context.view_layer.objects.active = rig_b
    result = bpy.ops.rex.generate_preview(profile_uid=save_uid)
    assert "FINISHED" in result
    owner, save_profile = addon.rigify_export.ui._profile_for_uid(save_uid)
    assert owner == rig_b and save_profile.preview_object is not None
    assert not addon.rigify_export.ui._control_rig_editable(save_profile)
    result = bpy.ops.rex.save_profile(profile_uid=save_uid)
    assert "FINISHED" in result
    owner, save_profile = addon.rigify_export.ui._profile_for_uid(save_uid)
    assert owner == rig_b and save_profile.configuration_valid
    assert save_profile.preview_object is None
    assert scene.rex_main_profile_uid == save_uid
    assert {entry["uid"] for entry in addon.rigify_export.standalone.embedded_profile_entries()} == {
        uid,
        save_uid,
    }
    selected_layout = _RecordingLayout()
    addon.ST_PT_panel.draw(SimpleNamespace(layout=selected_layout), bpy.context)
    assert selected_layout.box_calls == 1
    assert not any(label.startswith("Preview Rigify") for label in selected_layout.labels)

    addon.unregister()
    print("RIGIFY_FEATURE_INTEGRATION_OK")


if __name__ == "__main__":
    run()
