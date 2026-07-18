"""Blender background smoke tests for the ARP retarget preset editor."""

import os
import sys
import tempfile
from types import SimpleNamespace

import bpy


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import arp_retarget_preset as addon


def make_armature(name, bone_names):
    data = bpy.data.armatures.new(f"{name}_Data")
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for index, bone_name in enumerate(bone_names):
        bone = data.edit_bones.new(bone_name)
        bone.head = (0.0, 0.0, float(index))
        bone.tail = (0.0, 0.25, float(index))
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.select_set(False)
    return obj


def item_by_source(scene, source_name):
    return next(item for item in scene.arp_retarget_mapping_items if item.source_name == source_name)


def run():
    addon.register()
    scene = bpy.context.scene
    source_names = ("Arm.L", "Arm.R", "Leg_left", "Leg_right", "Center", "c_p_helper")
    target_names = ("CTRL.L", "CTRL.R", "Pole.L", "Pole.R", "c_hidden_helper")
    source = make_armature("Source", source_names)
    target = make_armature("Target", target_names)
    scene.arp_retarget_source_armature = source
    scene.arp_retarget_target_armature = target

    assert bpy.ops.script_toolkit.arp_build_bone_list() == {"FINISHED"}
    assert len(scene.arp_retarget_mapping_items) == len(source.data.bones)
    assert {item.source_name for item in scene.arp_retarget_mapping_items} == set(source_names)

    class FakeListLayout:
        def __init__(self, calls=None):
            self.calls = [] if calls is None else calls
            self.alignment = "EXPAND"

        def split(self, **_kwargs):
            return self

        def row(self, **_kwargs):
            child = FakeListLayout(self.calls)
            self.calls.append(("row", child))
            return child

        def operator(self, operator_id, **kwargs):
            self.calls.append(("operator", operator_id, kwargs))
            return SimpleNamespace(index=-1)

        def prop(self, item, property_name, **kwargs):
            self.calls.append(("prop", item, property_name, kwargs))

    fake_layout = FakeListLayout()
    fake_item = SimpleNamespace(source_name="Source", target_name="Target", selected=False)
    addon.STARP_UL_mapping.draw_item(None, None, fake_layout, None, fake_item, None, None, None, 0)
    operator_calls = [call for call in fake_layout.calls if call[0] == "operator"]
    assert [call[1] for call in operator_calls] == [
        addon.STARP_OT_select_mapping_row.bl_idname,
        addon.STARP_OT_target_mapping_cell.bl_idname,
    ]
    assert [call[2]["text"] for call in operator_calls] == ["Source", "Target"]
    assert fake_layout.alignment == "LEFT"
    assert not any(call[0] == "row" for call in fake_layout.calls)

    class FakeWindowManager:
        def __init__(self):
            self.operators = []
            self.dialogs = []

        def fileselect_add(self, operator):
            self.operators.append(operator)

        def invoke_props_dialog(self, operator, **kwargs):
            self.dialogs.append((operator, kwargs))
            return {"RUNNING_MODAL"}

    file_selector = FakeWindowManager()
    fake_context = SimpleNamespace(window_manager=file_selector)
    for operator_type in (addon.STARP_OT_import_bmap, addon.STARP_OT_export_bmap):
        operator = SimpleNamespace(filepath="")
        result = operator_type.invoke(operator, fake_context, None)
        assert result == {"RUNNING_MODAL"}, (
            f"{operator_type.__name__}.invoke incompatible return value: "
            f"expected a set, got {type(result).__name__} ({result!r})"
        )
    assert len(file_selector.operators) == 2

    addon._reset_target_click_state()
    target_operator = SimpleNamespace(index=0, new_name="", editing=False)
    target_event = SimpleNamespace(shift=False, ctrl=False, alt=False, value="PRESS")
    target_context = SimpleNamespace(scene=scene, window_manager=file_selector)
    assert addon.STARP_OT_target_mapping_cell.invoke(target_operator, target_context, target_event) == {"FINISHED"}
    assert [index for index, item in enumerate(scene.arp_retarget_mapping_items) if item.selected] == [0]

    double_click_operator = SimpleNamespace(index=0, new_name="", editing=False)
    double_click_event = SimpleNamespace(shift=False, ctrl=False, alt=False, value="DOUBLE_CLICK")
    assert addon.STARP_OT_target_mapping_cell.invoke(
        double_click_operator, target_context, double_click_event
    ) == {"RUNNING_MODAL"}
    double_click_operator.new_name = "Edited Target"
    assert addon.STARP_OT_target_mapping_cell.execute(double_click_operator, target_context) == {"FINISHED"}
    assert scene.arp_retarget_mapping_items[0].target_name == "Edited Target"
    assert scene.arp_retarget_mapping_items[0].target_manual

    addon._reset_target_click_state()
    ctrl_event = SimpleNamespace(shift=False, ctrl=True, alt=False, value="PRESS")
    alt_event = SimpleNamespace(shift=False, ctrl=False, alt=True, value="PRESS")
    for index in (2, 4):
        operator = SimpleNamespace(index=index, new_name="", editing=False)
        assert addon.STARP_OT_target_mapping_cell.invoke(operator, target_context, ctrl_event) == {"FINISHED"}
    assert [index for index, item in enumerate(scene.arp_retarget_mapping_items) if item.selected] == [0, 2, 4]
    operator = SimpleNamespace(index=2, new_name="", editing=False)
    assert addon.STARP_OT_target_mapping_cell.invoke(operator, target_context, alt_event) == {"FINISHED"}
    assert [index for index, item in enumerate(scene.arp_retarget_mapping_items) if item.selected] == [0, 4]

    bpy.ops.script_toolkit.arp_select_none()
    source_left = item_by_source(scene, "Arm.L")
    source_right = item_by_source(scene, "Arm.R")
    source_left.selected = True
    source_right.selected = True
    scene.arp_retarget_find = "Arm"
    scene.arp_retarget_replace = "Hand"
    scene.arp_retarget_prefix = "PRE_"
    scene.arp_retarget_suffix = "_SUF"
    assert bpy.ops.script_toolkit.arp_rename_source_to_target() == {"FINISHED"}
    assert source_left.target_name == "PRE_Hand.L_SUF"
    assert source_right.target_name == "PRE_Hand.R_SUF"

    scene.arp_retarget_find = "Hand"
    scene.arp_retarget_replace = "Forearm"
    scene.arp_retarget_prefix = "T_"
    scene.arp_retarget_suffix = "_X"
    assert bpy.ops.script_toolkit.arp_rename_target() == {"FINISHED"}
    assert source_left.target_name == "T_PRE_Forearm.L_SUF_X"
    assert source_right.target_name == "T_PRE_Forearm.R_SUF_X"

    bpy.ops.script_toolkit.arp_build_bone_list()
    preserved = item_by_source(scene, "Arm.L")
    preserved.target_name = "CTRL.L"
    preserved.target_manual = True
    preserved.location = True
    preserved.rot_add = (1.0, 2.0, 3.0)
    bpy.ops.script_toolkit.arp_select_none()
    manually_cleared = item_by_source(scene, "Arm.R")
    manually_cleared.target_name = "CTRL.R"
    manually_cleared.selected = True
    assert bpy.ops.script_toolkit.arp_clear_target() == {"FINISHED"}
    assert manually_cleared.target_manual
    source_updated = make_armature("SourceUpdated", source_names + ("Extra.L",))
    target_updated = make_armature("TargetUpdated", target_names + ("Extra.L", "Center", "Arm.R"))
    scene.arp_retarget_source_armature = source_updated
    scene.arp_retarget_target_armature = target_updated
    assert bpy.ops.script_toolkit.arp_update_bone_list() == {"FINISHED"}
    assert len(scene.arp_retarget_mapping_items) == len(source_updated.data.bones)
    assert len({item.source_name for item in scene.arp_retarget_mapping_items}) == len(source_updated.data.bones)
    preserved = item_by_source(scene, "Arm.L")
    assert preserved.target_name == "CTRL.L"
    assert preserved.location
    assert tuple(preserved.rot_add) == (1.0, 2.0, 3.0)
    assert item_by_source(scene, "Extra.L").target_name == "Extra.L"
    assert item_by_source(scene, "Center").target_name == "Center"
    assert item_by_source(scene, "Arm.R").target_name == ""
    assert item_by_source(scene, "Arm.R").target_manual

    scene.arp_retarget_source_armature = source
    scene.arp_retarget_target_armature = target
    bpy.ops.script_toolkit.arp_build_bone_list()

    addon._select_mapping_row(scene, 0)
    addon._select_mapping_row(scene, 2)
    assert [index for index, item in enumerate(scene.arp_retarget_mapping_items) if item.selected] == [2]
    bpy.ops.script_toolkit.arp_select_none()
    addon._select_mapping_row(scene, 1)
    addon._select_mapping_row(scene, 3, select_range=True)
    assert [index for index, item in enumerate(scene.arp_retarget_mapping_items) if item.selected] == [1, 2, 3]
    addon._select_mapping_row(scene, 5)
    assert [index for index, item in enumerate(scene.arp_retarget_mapping_items) if item.selected] == [5]
    bpy.ops.script_toolkit.arp_select_all()
    assert scene.arp_retarget_selection_anchor == -1
    addon._select_mapping_row(scene, 4, select_range=True)
    assert [index for index, item in enumerate(scene.arp_retarget_mapping_items) if item.selected] == [4]
    addon._select_mapping_row(scene, 1)
    addon._select_mapping_row(scene, 3, extend=True)
    addon._select_mapping_row(scene, 5, extend=True)
    assert [index for index, item in enumerate(scene.arp_retarget_mapping_items) if item.selected] == [1, 3, 5]
    assert scene.arp_retarget_mapping_index == 5
    addon._select_mapping_row(scene, 3, deselect=True)
    assert [index for index, item in enumerate(scene.arp_retarget_mapping_items) if item.selected] == [1, 5]
    assert scene.arp_retarget_mapping_index == 5
    addon._select_mapping_row(scene, 5, deselect=True)
    assert [index for index, item in enumerate(scene.arp_retarget_mapping_items) if item.selected] == [1]
    assert scene.arp_retarget_mapping_index == 1
    addon._select_mapping_row(scene, 1, deselect=True)
    assert not any(item.selected for item in scene.arp_retarget_mapping_items)
    assert scene.arp_retarget_mapping_index == -1
    assert addon._selected_or_active(scene) == []

    bpy.ops.script_toolkit.arp_select_none()
    swap_item = scene.arp_retarget_mapping_items[0]
    original_source = swap_item.source_name
    swap_item.target_name = "CTRL.L"
    swap_item.selected = True
    assert bpy.ops.script_toolkit.arp_swap_source_target() == {"FINISHED"}
    assert scene.arp_retarget_source_armature == target
    assert scene.arp_retarget_target_armature == source
    assert len(scene.arp_retarget_mapping_items) == len(target.data.bones)
    assert item_by_source(scene, "CTRL.L").target_name == original_source

    scene.arp_retarget_source_armature = source
    scene.arp_retarget_target_armature = target
    bpy.ops.script_toolkit.arp_build_bone_list()
    left = item_by_source(scene, "Arm.L")
    right = item_by_source(scene, "Arm.R")
    left.target_name = "CTRL.L"
    left.location = True
    left.ik = True
    left.ik_pole = "Pole.L"
    left.ik_world = True
    left.ik_auto_pole = "RELATIVE_CHAIN"
    left.ik_create_constraints = True
    left.ik_axis_correction = "-Y"
    left.set_as_root = True
    left.rot_add = (1.0, 2.0, 3.0)
    right.set_as_root = False
    right.rot_add = (0.0, 0.0, 0.0)

    assert bpy.ops.script_toolkit.arp_mirror_bone_list(mirror_dir="LEFT_TO_RIGHT") == {"FINISHED"}
    assert right.target_name == "CTRL.R"
    assert right.location and right.ik and right.ik_world and right.ik_create_constraints
    assert right.ik_pole == "Pole.R"
    assert right.ik_auto_pole == "RELATIVE_CHAIN"
    assert right.ik_axis_correction == "-Y"
    assert not right.set_as_root
    assert tuple(right.rot_add) == (0.0, 0.0, 0.0)

    assert addon._mirror_name("Bip001 L Arm", "LEFT_TO_RIGHT") == "Bip001 R Arm"
    assert addon._mirror_name("calf_left.001", "LEFT_TO_RIGHT") == "calf_right.001"
    assert addon._mirror_name("Hand-R", "RIGHT_TO_LEFT") == "Hand-L"
    assert hasattr(addon, "STARP_OT_rename_target")

    import_path = os.path.join(tempfile.gettempdir(), "script_toolkit_anchor_test.bmap")
    with open(import_path, "w", encoding="utf-8", newline="\n") as preset:
        preset.write("CTRL.L%False%ABSOLUTE%0,0,0%0,0,0%1%False%False%Y%\n")
        preset.write("Arm.L\nFalse\nFalse\n\n")
    scene.arp_retarget_selection_anchor = 3
    assert bpy.ops.script_toolkit.arp_import_bmap(filepath=import_path) == {"FINISHED"}
    assert scene.arp_retarget_selection_anchor == -1
    os.remove(import_path)

    addon.unregister()
    print("ARP_RETARGET_TESTS_OK")


if __name__ == "__main__":
    run()
