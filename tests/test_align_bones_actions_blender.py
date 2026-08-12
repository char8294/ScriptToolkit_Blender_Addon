"""Blender background tests for Align Bones quick actions."""

import importlib.util
import sys
from pathlib import Path

import bpy


def load_addon():
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


def make_armature(name):
    armature_data = bpy.data.armatures.new(f"{name}_Data")
    armature = bpy.data.objects.new(name, armature_data)
    bpy.context.scene.collection.objects.link(armature)
    return armature


def run():
    addon = load_addon()
    addon.register()
    armature = make_armature("AlignBonesActionsRig")

    try:
        bpy.context.view_layer.objects.active = armature
        armature.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")

        parent = armature.data.edit_bones.new("Parent")
        parent.head = (0.0, 0.0, 0.0)
        parent.tail = (0.0, 1.0, 0.0)
        child = armature.data.edit_bones.new("Child")
        child.head = parent.tail
        child.tail = (0.0, 2.0, 0.0)
        child.select = True
        parent.select = False

        bpy.ops.script_toolkit.connect_touching_bones()
        assert child.parent == parent
        assert child.use_connect is True

        exact_name = armature.data.edit_bones.new("jts ex")
        exact_name.head = (2.0, 0.0, 0.0)
        exact_name.tail = (2.0, 1.0, 0.0)
        case_variant = armature.data.edit_bones.new("JTS EX")
        case_variant.head = (3.0, 0.0, 0.0)
        case_variant.tail = (3.0, 1.0, 0.0)
        matching_prefix = armature.data.edit_bones.new("prefix jts ex")
        matching_prefix.head = (4.0, 0.0, 0.0)
        matching_prefix.tail = (4.0, 1.0, 0.0)
        unrelated_name = armature.data.edit_bones.new("prefix jts")
        unrelated_name.head = (4.0, 2.0, 0.0)
        unrelated_name.tail = (4.0, 3.0, 0.0)

        assert bpy.ops.script_toolkit.delete_jts_ex_bones() == {"FINISHED"}
        remaining_names = {bone.name for bone in armature.data.edit_bones}
        assert "jts ex" not in remaining_names
        assert "JTS EX" not in remaining_names
        assert "prefix jts ex" not in remaining_names
        assert "prefix jts" in remaining_names
        assert addon.align_bones.ST_OT_DeleteJtsExBones.bl_idname == "script_toolkit.delete_jts_ex_bones"

        connect_name = armature.data.edit_bones.new("connect")
        connect_name.head = (5.0, 0.0, 0.0)
        connect_name.tail = (5.0, 1.0, 0.0)
        connect_case_variant = armature.data.edit_bones.new("CONNECT")
        connect_case_variant.head = (6.0, 0.0, 0.0)
        connect_case_variant.tail = (6.0, 1.0, 0.0)
        connect_matching_prefix = armature.data.edit_bones.new("prefix connect")
        connect_matching_prefix.head = (7.0, 0.0, 0.0)
        connect_matching_prefix.tail = (7.0, 1.0, 0.0)
        connect_unrelated_name = armature.data.edit_bones.new("prefix con")
        connect_unrelated_name.head = (7.0, 2.0, 0.0)
        connect_unrelated_name.tail = (7.0, 3.0, 0.0)

        assert bpy.ops.script_toolkit.delete_connect_bone() == {"FINISHED"}
        remaining_names = {bone.name for bone in armature.data.edit_bones}
        assert "connect" not in remaining_names
        assert "CONNECT" not in remaining_names
        assert "prefix connect" not in remaining_names
        assert "prefix con" in remaining_names
        assert addon.align_bones.ST_OT_DeleteConnectBone.bl_idname == "script_toolkit.delete_connect_bone"
        print("ALIGN_BONES_QUICK_ACTIONS_OK")
    finally:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        armature_data = armature.data
        bpy.data.objects.remove(armature, do_unlink=True)
        bpy.data.armatures.remove(armature_data)
        addon.unregister()


if __name__ == "__main__":
    run()
