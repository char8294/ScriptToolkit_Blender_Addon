"""Blender background test for the TinyK Rig Manual symmetry rename operator."""

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
    return obj


def run():
    addon = load_addon()
    addon.register()
    try:
        original_names = (
            "leg LF jts",
            "leg RF jts",
            "leg LB jts",
            "leg RB jts",
            "paw LF",
            "paw RF",
            "paw LB",
            "paw RB",
            "finger L jts",
            "finger R jts",
            "hand L",
            "hand R",
            "connect L",
            "tailex",
            "helper jtsex",
            "Bone L",
            "center",
        )
        armature = make_armature("TinyK_Test", original_names)

        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        bpy.context.view_layer.objects.active = armature

        assert bpy.ops.script_toolkit.tinyk_rename_symmetry_names() == {"FINISHED"}
        assert set(armature.data.bones.keys()) == {
            "leg Front.L",
            "leg Front.R",
            "leg Back.L",
            "leg Back.R",
            "paw Front.L",
            "paw Front.R",
            "paw Back.L",
            "paw Back.R",
            "finger.L",
            "finger.R",
            "hand.L",
            "hand.R",
            "connect L",
            "tailex",
            "helper jtsex",
            "Bone L",
            "center",
        }
        print("TINYK_SYMMETRY_NAMES_OK")
    finally:
        addon.unregister()


if __name__ == "__main__":
    run()
