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

    class FakeWindowManager:
        def __init__(self):
            self.operators = []

        def fileselect_add(self, operator):
            self.operators.append(operator)

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
    assert not hasattr(addon, "STARP_OT_rename_target")

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
