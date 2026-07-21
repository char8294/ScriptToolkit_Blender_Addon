"""Biped Names Helper, embedded in Script Toolkit without a separate panel tab."""

import json

import bpy
from bpy.types import Operator, UIList


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


class STBN_OT_batch_rename(Operator):
    bl_idname = "script_toolkit.biped_batch_rename"
    bl_label = "Batch Rename"
    bl_description = "Rename Bones or Vertex Groups based on Find/Replace/Suffix"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.script_toolkit
        armatures, meshes = _related_objects(context)
        
        rules = []
        if props.rename_find_1:
            rules.append((props.rename_find_1, props.rename_replace_1, props.rename_suffix_1))
        if props.rename_find_2:
            rules.append((props.rename_find_2, props.rename_replace_2, props.rename_suffix_2))
            
        if not rules:
            return {"FINISHED"}
            
        if props.rename_target == "BONE":
            for arm in armatures:
                for bone in arm.data.bones:
                    for fnd, rep, suf in rules:
                        if fnd in bone.name:
                            bone.name = bone.name.replace(fnd, rep) + suf
                            break
                            
        elif props.rename_target == "VERTEX_GROUP":
            for mesh in meshes:
                for vg in mesh.vertex_groups:
                    for fnd, rep, suf in rules:
                        if fnd in vg.name:
                            try:
                                vg.name = vg.name.replace(fnd, rep) + suf
                            except Exception:
                                pass
                            break
                            
        return {"FINISHED"}


class STBN_OT_add_vg_prefix(Operator):
    bl_idname = "script_toolkit.biped_add_vg_prefix"
    bl_label = "Add Prefix to Vertex Groups"
    bl_description = "Add Prefix to all Vertex Groups in selected meshes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.script_toolkit
        _, meshes = _related_objects(context)
        prefix = props.vg_prefix
        
        if not prefix:
            return {"FINISHED"}
            
        for mesh in meshes:
            for vg in mesh.vertex_groups:
                if not vg.name.startswith(prefix):
                    try:
                        vg.name = prefix + vg.name
                    except Exception:
                        pass
                    
        return {"FINISHED"}


class STBN_UL_preview_list(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        split = layout.split(factor=0.5)
        split.label(text=item.old_name, icon="FORWARD")
        split.label(text=item.new_name)


class STBN_OT_generate_preview(Operator):
    bl_idname = "script_toolkit.biped_generate_preview"
    bl_label = "Generate Preview"
    bl_description = "Preview name changes before applying"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.script_toolkit
        armatures, meshes = _related_objects(context)
        
        rules = []
        if props.rename_find_1:
            rules.append((props.rename_find_1, props.rename_replace_1, props.rename_suffix_1))
        if props.rename_find_2:
            rules.append((props.rename_find_2, props.rename_replace_2, props.rename_suffix_2))
            
        props.preview_items.clear()
        count = 0
        
        if props.rename_target == "BONE":
            for arm in armatures:
                for bone in arm.data.bones:
                    for fnd, rep, suf in rules:
                        if fnd in bone.name:
                            new_name = bone.name.replace(fnd, rep) + suf
                            if new_name != bone.name:
                                item = props.preview_items.add()
                                item.old_name = bone.name
                                item.new_name = new_name
                                count += 1
                            break
                            
        elif props.rename_target == "VERTEX_GROUP":
            prefix = props.vg_prefix
            for mesh in meshes:
                for vg in mesh.vertex_groups:
                    new_name = vg.name
                    # Apply batch rename rules
                    for fnd, rep, suf in rules:
                        if fnd in new_name:
                            new_name = new_name.replace(fnd, rep) + suf
                            break
                    # Apply prefix
                    if prefix and not new_name.startswith(prefix):
                        new_name = prefix + new_name
                        
                    if new_name != vg.name:
                        item = props.preview_items.add()
                        item.old_name = vg.name
                        item.new_name = new_name
                        count += 1
                        
        props.preview_summary = f"{count} items will be changed"
        return {"FINISHED"}


def draw_ui(layout, context):
    props = context.scene.script_toolkit
    
    active_obj = context.active_object
    arm = active_obj.data if (active_obj and active_obj.type == 'ARMATURE') else None

    arm_box = layout.box()
    sub = arm_box.column()
    if arm:
        row = sub.row()
        row.prop(arm, "show_names", text="Show Names", toggle=True, icon='VIS_SEL_11')
        row.prop(arm, "show_axes", text="Show Axes", toggle=True, icon='AXIS_SIDE')
        if hasattr(arm, "axes_position"):
            sub.prop(arm, "axes_position", text="Axes Position")
    else:
        sub.active = False
        row = sub.row()
        row.label(text="Show Names", icon='VIS_SEL_11')
        row.label(text="Show Axes", icon='AXIS_SIDE')
        sub.label(text="Axes Position")

    layout.separator()
    
    col = layout.column(align=True)
    col.operator("script_toolkit.biped_setup_mirror", icon="MOD_MIRROR")
    col.operator("script_toolkit.biped_restore_names", icon="LOOP_BACK")
    
    layout.separator()
    
    box = layout.box()
    box.prop(props, "rename_target")
    row = box.row()
    col1 = row.column(align=True)
    col1.prop(props, "rename_find_2")
    col1.prop(props, "rename_replace_2")
    col1.prop(props, "rename_suffix_2")
    
    col2 = row.column(align=True)
    col2.prop(props, "rename_find_1")
    col2.prop(props, "rename_replace_1")
    col2.prop(props, "rename_suffix_1")
    
    box.operator("script_toolkit.biped_batch_rename", icon="FONT_DATA")
    
    layout.separator()
    
    box_vg = layout.box()
    row_vg = box_vg.row(align=True)
    row_vg.prop(props, "vg_prefix")
    row_vg.operator("script_toolkit.biped_add_vg_prefix", icon="GROUP_VERTEX", text="Add Prefix")

    layout.separator()
    
    box_prev = layout.box()
    box_prev.operator("script_toolkit.biped_generate_preview", icon="FILE_TICK")
    if props.preview_summary:
        box_prev.label(text=props.preview_summary, icon="INFO")
    if len(props.preview_items) > 0:
        box_prev.template_list("STBN_UL_preview_list", "", props, "preview_items", props, "preview_index", rows=15)


CLASSES = (STBN_OT_setup_mirror, STBN_OT_restore_names, STBN_OT_batch_rename, STBN_OT_add_vg_prefix, STBN_UL_preview_list, STBN_OT_generate_preview)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
