"""KJ Export batch FBX tools integrated into Script Toolkit."""

import json
import os

import bpy


def get_preset_path():
    appdata = os.getenv("APPDATA")
    if not appdata:
        return ""
    base_path = os.path.join(appdata, "Blender Foundation", "Blender")
    version = f"{bpy.app.version[0]}.{bpy.app.version[1]}"
    return os.path.join(
        base_path,
        version,
        "scripts",
        "presets",
        "operator",
        "better_export.fbx",
    )


def get_presets(self, context):
    del self, context
    preset_dir = get_preset_path()
    items = [("NONE", "No Preset", "Do not use a specific preset")]
    if os.path.exists(preset_dir):
        for filename in sorted(os.listdir(preset_dir)):
            if filename.endswith(".py"):
                name = filename[:-3]
                items.append((name, name, f"Use {name} preset"))
    return items


class BATCH_FBX_MeshItem(bpy.types.PropertyGroup):
    obj: bpy.props.PointerProperty(type=bpy.types.Object, name="Mesh")


class BATCH_FBX_Properties(bpy.types.PropertyGroup):
    export_dir: bpy.props.StringProperty(
        name="Export Directory",
        description="Select folder to export files",
        default="",
        maxlen=1024,
        subtype="DIR_PATH",
    )
    preset_enum: bpy.props.EnumProperty(
        name="Preset",
        description="Select Better FBX preset to use",
        items=get_presets,
    )
    target_armature: bpy.props.PointerProperty(
        type=bpy.types.Object,
        name="Armature",
        description="Pin the armature to pair with the meshes",
        poll=lambda self, obj: obj.type == "ARMATURE",
    )
    mesh_list: bpy.props.CollectionProperty(type=BATCH_FBX_MeshItem)
    mesh_list_index: bpy.props.IntProperty()
    force_shade_smooth: bpy.props.BoolProperty(
        name="Force Shade Smooth",
        description="Clear sharp edges/custom normals and apply smooth shading for the exported file only",
        default=False,
    )
    restore_biped_names: bpy.props.BoolProperty(
        name="Restore Biped Names",
        description="Export with original Biped bone names from biped_name_mapping",
        default=False,
    )


class BATCH_FBX_UL_mesh_list(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        del context, data, active_data, active_propname, index
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            if item.obj:
                layout.label(text=item.obj.name, icon="MESH_DATA")
            else:
                layout.label(text="<Deleted Object>", icon="ERROR")


class BATCH_FBX_OT_add_meshes(bpy.types.Operator):
    bl_idname = "export.batch_better_fbx_add_mesh"
    bl_label = "Add Selected Meshes"
    bl_description = "Add currently selected meshes to the export list"

    def execute(self, context):
        props = context.scene.batch_better_fbx_props
        existing_objs = {item.obj for item in props.mesh_list if item.obj}
        added = 0
        for obj in context.selected_objects:
            if obj.type == "MESH" and obj not in existing_objs:
                item = props.mesh_list.add()
                item.obj = obj
                existing_objs.add(obj)
                added += 1
        if added:
            self.report({"INFO"}, f"Added {added} meshes to the list.")
        else:
            self.report({"WARNING"}, "No new meshes selected.")
        return {"FINISHED"}


class BATCH_FBX_OT_remove_mesh(bpy.types.Operator):
    bl_idname = "export.batch_better_fbx_remove_mesh"
    bl_label = "Remove Mesh"
    bl_description = "Remove the selected mesh from the export list"

    def execute(self, context):
        props = context.scene.batch_better_fbx_props
        index = props.mesh_list_index
        if 0 <= index < len(props.mesh_list):
            props.mesh_list.remove(index)
            props.mesh_list_index = min(index, max(0, len(props.mesh_list) - 1))
        return {"FINISHED"}


class BATCH_FBX_OT_clear_meshes(bpy.types.Operator):
    bl_idname = "export.batch_better_fbx_clear_meshes"
    bl_label = "Clear List"
    bl_description = "Clear all meshes from the export list"

    def execute(self, context):
        context.scene.batch_better_fbx_props.mesh_list.clear()
        return {"FINISHED"}


def _load_preset_parameters(preset_name):
    if preset_name == "NONE":
        return {}
    preset_path = os.path.join(get_preset_path(), preset_name + ".py")
    if not os.path.exists(preset_path):
        return {}

    class MockOp:
        pass

    mock_op = MockOp()
    with open(preset_path, "r", encoding="utf-8") as preset_file:
        code = preset_file.read().replace(
            "op = bpy.context.active_operator", "op = mock_op"
        )
    exec(code, {"bpy": bpy, "mock_op": mock_op})
    return {
        attr: getattr(mock_op, attr)
        for attr in dir(mock_op)
        if not attr.startswith("__") and attr != "filepath"
    }


class BATCH_FBX_OT_export(bpy.types.Operator):
    bl_idname = "export.batch_better_fbx"
    bl_label = "Batch Export FBX"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.batch_better_fbx_props
        export_dir = bpy.path.abspath(props.export_dir)
        if not export_dir:
            self.report({"ERROR"}, "Please select an export directory.")
            return {"CANCELLED"}
        if not os.path.exists(export_dir):
            try:
                os.makedirs(export_dir)
            except OSError as error:
                self.report({"ERROR"}, f"Could not create directory: {error}")
                return {"CANCELLED"}

        armature = props.target_armature
        if not armature:
            self.report({"ERROR"}, "Please pin a Target Armature.")
            return {"CANCELLED"}
        if not props.mesh_list:
            self.report({"ERROR"}, "The export list is empty. Please add meshes.")
            return {"CANCELLED"}

        meshes = [item.obj for item in props.mesh_list if item.obj and item.obj.type == "MESH"]
        if not meshes:
            self.report({"ERROR"}, "Export list contains no valid meshes.")
            return {"CANCELLED"}

        try:
            preset_params = _load_preset_parameters(props.preset_enum)
        except Exception as error:
            preset_params = {}
            self.report({"WARNING"}, f"Failed to load preset: {error}")

        original_active = context.view_layer.objects.active
        original_selected = list(context.selected_objects)
        temp_armature = None
        export_armature = armature
        bone_mapping = None
        restore_biped = props.restore_biped_names

        if restore_biped and "biped_name_mapping" in armature.data:
            try:
                bone_mapping = json.loads(armature.data["biped_name_mapping"])
            except (TypeError, ValueError):
                bone_mapping = None
            if bone_mapping:
                temp_arm_data = armature.data.copy()
                temp_armature = armature.copy()
                temp_armature.data = temp_arm_data
                if temp_armature.animation_data:
                    temp_armature.animation_data_clear()
                context.collection.objects.link(temp_armature)
                for new_name, old_name in bone_mapping.items():
                    bone = temp_arm_data.bones.get(new_name)
                    if bone:
                        bone.name = old_name
                export_armature = temp_armature

        count = 0
        errors = []
        try:
            for mesh in meshes:
                bpy.ops.object.select_all(action="DESELECT")
                temp_mesh = None
                export_mesh = mesh
                original_name = mesh.name
                need_duplicate = props.force_shade_smooth or restore_biped

                if need_duplicate:
                    mesh.select_set(True)
                    context.view_layer.objects.active = mesh
                    bpy.ops.object.duplicate(linked=False)
                    export_mesh = context.active_object
                    temp_mesh = export_mesh

                    if props.force_shade_smooth:
                        if temp_mesh.data.has_custom_normals:
                            try:
                                bpy.ops.mesh.customdata_custom_splitnormals_clear()
                            except RuntimeError:
                                pass
                        for modifier in list(temp_mesh.modifiers):
                            is_smooth_nodes = (
                                modifier.type == "NODES"
                                and modifier.node_group
                                and "Smooth by Angle" in modifier.node_group.name
                            )
                            is_smooth_name = "Smooth by Angle" in modifier.name
                            if is_smooth_nodes or is_smooth_name or modifier.type == "EDGE_SPLIT":
                                temp_mesh.modifiers.remove(modifier)
                        for polygon in temp_mesh.data.polygons:
                            polygon.use_smooth = True
                        for edge in temp_mesh.data.edges:
                            edge.use_edge_sharp = False
                        temp_mesh.data.update()

                    if restore_biped and bone_mapping:
                        vertex_group_mapping = bone_mapping
                        if "biped_vg_mapping" in mesh:
                            try:
                                vertex_group_mapping = json.loads(mesh["biped_vg_mapping"])
                            except (TypeError, ValueError):
                                pass
                        for new_name, old_name in vertex_group_mapping.items():
                            vertex_group = temp_mesh.vertex_groups.get(new_name)
                            if vertex_group:
                                vertex_group.name = old_name
                        if temp_armature:
                            for modifier in temp_mesh.modifiers:
                                if modifier.type == "ARMATURE" and modifier.object == armature:
                                    modifier.object = temp_armature

                    mesh.name = original_name + "_temp_export"
                    export_mesh.name = original_name
                    bpy.ops.object.select_all(action="DESELECT")

                export_armature.select_set(True)
                export_mesh.select_set(True)
                context.view_layer.objects.active = export_mesh
                export_kwargs = preset_params.copy()
                export_kwargs.update(filepath=os.path.join(export_dir, original_name + ".fbx"), use_selection=True)
                try:
                    bpy.ops.better_export.fbx(**export_kwargs)
                    count += 1
                except Exception as error:
                    errors.append(f"{original_name}: {error}")

                if temp_mesh:
                    temp_data = temp_mesh.data
                    bpy.data.objects.remove(temp_mesh, do_unlink=True)
                    if temp_data.users == 0:
                        bpy.data.meshes.remove(temp_data)
                    mesh.name = original_name
        finally:
            if temp_armature:
                temp_arm_data = temp_armature.data
                bpy.data.objects.remove(temp_armature, do_unlink=True)
                if temp_arm_data.users == 0:
                    bpy.data.armatures.remove(temp_arm_data)
            bpy.ops.object.select_all(action="DESELECT")
            for obj in original_selected:
                if obj and obj.name in bpy.data.objects:
                    obj.select_set(True)
            context.view_layer.objects.active = original_active

        if errors:
            self.report({"WARNING"}, f"Exported {count} files. Errors in: {', '.join(errors)}")
        else:
            self.report({"INFO"}, f"Successfully exported {count} files to {export_dir}")
        return {"FINISHED"}


def draw_ui(layout, context):
    """Draw the KJ Export controls inside Script Toolkit's main panel."""
    props = context.scene.batch_better_fbx_props

    box = layout.box()
    box.label(text="Target Armature", icon="ARMATURE_DATA")
    box.prop(props, "target_armature", text="")

    box = layout.box()
    box.label(text="Meshes to Export", icon="MESH_DATA")
    row = box.row()
    row.template_list(
        "BATCH_FBX_UL_mesh_list",
        "",
        props,
        "mesh_list",
        props,
        "mesh_list_index",
        rows=5,
    )
    controls = row.column(align=True)
    controls.operator(BATCH_FBX_OT_add_meshes.bl_idname, icon="ADD", text="")
    controls.operator(BATCH_FBX_OT_remove_mesh.bl_idname, icon="REMOVE", text="")
    controls.separator()
    controls.operator(BATCH_FBX_OT_clear_meshes.bl_idname, icon="TRASH", text="")

    box = layout.box()
    box.label(text="Export Settings", icon="EXPORT")
    box.prop(props, "export_dir")
    box.prop(props, "preset_enum", text="Preset")
    options = box.box()
    options.label(text="Options", icon="OPTIONS")
    options.prop(props, "force_shade_smooth")
    options.prop(props, "restore_biped_names")

    export_row = layout.row()
    export_row.scale_y = 1.5
    export_row.operator(BATCH_FBX_OT_export.bl_idname, icon="EXPORT", text="KJ Export Batch")


classes = (
    BATCH_FBX_MeshItem,
    BATCH_FBX_Properties,
    BATCH_FBX_UL_mesh_list,
    BATCH_FBX_OT_add_meshes,
    BATCH_FBX_OT_remove_mesh,
    BATCH_FBX_OT_clear_meshes,
    BATCH_FBX_OT_export,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.batch_better_fbx_props = bpy.props.PointerProperty(type=BATCH_FBX_Properties)


def unregister():
    if hasattr(bpy.types.Scene, "batch_better_fbx_props"):
        del bpy.types.Scene.batch_better_fbx_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
