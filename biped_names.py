"""Biped Names Helper, embedded in Script Toolkit without a separate panel tab."""

import json

import bpy
from bpy.types import Operator


def _all_fcurves(action):
    curves = []
    if hasattr(action, "is_action_layered") and action.is_action_layered:
        for layer in action.layers:
            for strip in layer.strips:
                if hasattr(strip, "channelbags"):
                    for bag in strip.channelbags:
                        curves.extend(bag.fcurves)
    elif hasattr(action, "fcurves"):
        curves.extend(action.fcurves)
    return curves


def _rename_action_paths(mapping):
    if not mapping:
        return
    paths = [(f'pose.bones["{old}"]', f'pose.bones["{new}"]') for new, old in mapping.items()]
    names = {old: new for new, old in mapping.items()}
    for action in bpy.data.actions:
        for curve in _all_fcurves(action):
            for old_path, new_path in paths:
                if old_path in curve.data_path:
                    curve.data_path = curve.data_path.replace(old_path, new_path)
            if curve.group and curve.group.name in names:
                curve.group.name = names[curve.group.name]


def _restore_action_paths(mapping):
    if not mapping:
        return
    paths = [(f'pose.bones["{new}"]', f'pose.bones["{old}"]') for new, old in mapping.items()]
    names = {new: old for new, old in mapping.items()}
    for action in bpy.data.actions:
        for curve in _all_fcurves(action):
            for current_path, original_path in paths:
                if current_path in curve.data_path:
                    curve.data_path = curve.data_path.replace(current_path, original_path)
            if curve.group and curve.group.name in names:
                curve.group.name = names[curve.group.name]


def _standard_name(name):
    if " L " in name:
        return name.replace(" L ", " ") + ".L"
    if " R " in name:
        return name.replace(" R ", " ") + ".R"
    return None


def rename_to_standard(obj):
    if not obj:
        return
    mapping = {}
    if obj.type == "ARMATURE":
        for bone in obj.data.bones:
            new_name = _standard_name(bone.name)
            if new_name:
                mapping[new_name] = bone.name
                bone.name = new_name
        if mapping:
            obj.data["biped_name_mapping"] = json.dumps(mapping)
            _rename_action_paths(mapping)
    elif obj.type == "MESH":
        for group in obj.vertex_groups:
            new_name = _standard_name(group.name)
            if new_name:
                mapping[new_name] = group.name
                try:
                    group.name = new_name
                except Exception:
                    pass
        if mapping:
            obj["biped_vg_mapping"] = json.dumps(mapping)


def restore_original_names(obj):
    if not obj:
        return
    if obj.type == "ARMATURE" and "biped_name_mapping" in obj.data:
        try:
            mapping = json.loads(obj.data["biped_name_mapping"])
            _restore_action_paths(mapping)
            for new_name, old_name in mapping.items():
                if new_name in obj.data.bones:
                    obj.data.bones[new_name].name = old_name
            del obj.data["biped_name_mapping"]
        except Exception:
            pass
    elif obj.type == "MESH" and "biped_vg_mapping" in obj:
        try:
            mapping = json.loads(obj["biped_vg_mapping"])
            for new_name, old_name in mapping.items():
                if new_name in obj.vertex_groups:
                    try:
                        obj.vertex_groups[new_name].name = old_name
                    except Exception:
                        pass
            del obj["biped_vg_mapping"]
        except Exception:
            pass


def _related_objects(context):
    armatures, meshes = set(), set()
    for obj in context.selected_objects:
        if obj.type == "ARMATURE":
            armatures.add(obj)
        elif obj.type == "MESH":
            meshes.add(obj)
        if obj.parent and obj.parent.type == "ARMATURE":
            armatures.add(obj.parent)
        for child in obj.children:
            if child.type == "MESH":
                meshes.add(child)
    return armatures, meshes


class STBN_OT_setup_mirror(Operator):
    bl_idname = "script_toolkit.biped_setup_mirror"
    bl_label = "Setup Symmetry Names"
    bl_description = "Convert ' L '/' R ' to '.L'/'.R' and save original names"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armatures, meshes = _related_objects(context)
        for armature in armatures:
            rename_to_standard(armature)
        for mesh in meshes:
            rename_to_standard(mesh)
        return {"FINISHED"}


class STBN_OT_restore_names(Operator):
    bl_idname = "script_toolkit.biped_restore_names"
    bl_label = "Restore Original Names"
    bl_description = "Restore names from saved properties and clean up"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armatures, meshes = _related_objects(context)
        for armature in armatures:
            restore_original_names(armature)
        for mesh in meshes:
            restore_original_names(mesh)
        return {"FINISHED"}


def draw_ui(layout, _context):
    col = layout.column(align=True)
    col.operator("script_toolkit.biped_setup_mirror", icon="MOD_MIRROR")
    col.operator("script_toolkit.biped_restore_names", icon="LOOP_BACK")


CLASSES = (STBN_OT_setup_mirror, STBN_OT_restore_names)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
