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
    remove_unused_bones: bpy.props.BoolProperty(
        name="Remove Unused Bones",
        description="Remove bones without skin weights from each exported mesh while preserving weighted bones and their parent chains",
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


def _weighted_bone_names_with_ancestors(mesh, armature):
    """Return weighted bone names plus every parent needed by this mesh."""
    group_names = {group.index: group.name for group in mesh.vertex_groups}
    weighted_bones = set()
    for vertex in mesh.data.vertices:
        for assignment in vertex.groups:
            if assignment.weight <= 0.0:
                continue
            bone_name = group_names.get(assignment.group)
            bone = armature.data.bones.get(bone_name) if bone_name else None
            if bone:
                weighted_bones.add(bone.name)

    names_to_keep = set(weighted_bones)
    for bone_name in weighted_bones:
        bone = armature.data.bones.get(bone_name)
        while bone and bone.parent:
            bone = bone.parent
            names_to_keep.add(bone.name)
    return names_to_keep


def _duplicate_armature(context, armature):
    """Create a scene-linked independent armature copy for one export."""
    temp_armature_data = armature.data.copy()
    temp_armature = armature.copy()
    temp_armature.data = temp_armature_data
    if temp_armature.animation_data:
        temp_armature.animation_data_clear()
    context.collection.objects.link(temp_armature)
    return temp_armature


def _duplicate_mesh(context, mesh):
    """Create a scene-linked independent mesh copy without relying on UI context."""
    temp_mesh_data = mesh.data.copy()
    temp_mesh = mesh.copy()
    temp_mesh.data = temp_mesh_data
    if temp_mesh.animation_data:
        temp_mesh.animation_data_clear()
    context.collection.objects.link(temp_mesh)
    return temp_mesh


def _remove_bones_except(context, armature, names_to_keep):
    """Remove all other bones from a temporary armature and restore selection."""
    original_active = context.view_layer.objects.active
    original_selected = list(context.selected_objects)
    removed_count = 0
    try:
        if original_active and original_active.mode != "OBJECT" and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode="EDIT")
        for bone in list(armature.data.edit_bones):
            if bone.name not in names_to_keep:
                armature.data.edit_bones.remove(bone)
                removed_count += 1
    finally:
        if armature.mode != "OBJECT" and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for obj in original_selected:
            if obj and obj.name in bpy.data.objects:
                obj.select_set(True)
        if original_active and original_active.name in bpy.data.objects:
            context.view_layer.objects.active = original_active
    return removed_count


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
        original_mode = original_active.mode if original_active else "OBJECT"
        bone_mapping = None
        restore_biped = props.restore_biped_names
        if restore_biped and "biped_name_mapping" in armature.data:
            try:
                bone_mapping = json.loads(armature.data["biped_name_mapping"])
            except (TypeError, ValueError):
                bone_mapping = None
        apply_biped_names = bool(restore_biped and bone_mapping)
        remove_unused_bones = props.remove_unused_bones

        count = 0
        errors = []
        try:
            # Edit-mode changes (including Mesh > Sort Elements) live in the
            # edit BMesh until Blender syncs them back to the mesh datablock.
            # The temporary export copies below must be made after that sync.
            if original_mode != "OBJECT":
                for obj in original_selected:
                    if obj.type == "MESH" and obj.mode == "EDIT":
                        obj.update_from_editmode()
                if not bpy.ops.object.mode_set.poll():
                    self.report(
                        {"ERROR"},
                        "Could not switch to Object Mode to synchronize mesh edits.",
                    )
                    return {"CANCELLED"}
                bpy.ops.object.mode_set(mode="OBJECT")

            for mesh in meshes:
                temp_mesh = None
                temp_armature = None
                export_mesh = mesh
                export_armature = armature
                original_name = mesh.name
                original_data_name = mesh.data.name
                source_mesh_renamed = False
                source_mesh_data_renamed = False
                try:
                    if apply_biped_names or remove_unused_bones:
                        temp_armature = _duplicate_armature(context, armature)
                        export_armature = temp_armature
                        if remove_unused_bones:
                            names_to_keep = _weighted_bone_names_with_ancestors(mesh, armature)
                            _remove_bones_except(context, temp_armature, names_to_keep)
                        if apply_biped_names:
                            for new_name, old_name in bone_mapping.items():
                                bone = temp_armature.data.bones.get(new_name)
                                if bone:
                                    bone.name = old_name

                    need_duplicate = (
                        props.force_shade_smooth
                        or apply_biped_names
                        or remove_unused_bones
                    )
                    bpy.ops.object.select_all(action="DESELECT")
                    if not need_duplicate:
                        export_mesh = mesh
                    else:
                        export_mesh = _duplicate_mesh(context, mesh)
                        temp_mesh = export_mesh

                    if temp_mesh and props.force_shade_smooth:
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

                    if temp_mesh and apply_biped_names:
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

                    if temp_mesh and temp_armature:
                        for modifier in temp_mesh.modifiers:
                            if modifier.type == "ARMATURE" and modifier.object == armature:
                                modifier.object = temp_armature

                    # Blender appends ".001" to both the duplicated object and its
                    # mesh data while the source owns the original names.  Give the
                    # source temporary names so the exporter always sees the
                    # selected copy with the original names, including when
                    # remove_unused_bones requires a temporary armature.
                    if temp_mesh:
                        mesh.name = original_name + "_temp_export"
                        export_mesh.name = original_name
                        source_mesh_renamed = True
                        mesh.data.name = original_data_name + "_temp_export"
                        export_mesh.data.name = original_data_name
                        source_mesh_data_renamed = True

                    # Better FBX evaluates selected objects through the
                    # dependency graph when Apply Modifiers is enabled.  Data
                    # API copies and modifier retargeting must be explicitly
                    # tagged before that evaluation or Blender can hand the
                    # exporter the copy's previous evaluated mesh.
                    export_mesh.data.update()
                    export_mesh.update_tag(refresh={"OBJECT", "DATA"})
                    export_armature.update_tag(refresh={"OBJECT", "DATA"})
                    context.view_layer.update()

                    bpy.ops.object.select_all(action="DESELECT")
                    export_armature.select_set(True)
                    export_mesh.select_set(True)
                    context.view_layer.objects.active = export_mesh
                    export_kwargs = preset_params.copy()
                    export_kwargs.update(
                        filepath=os.path.join(export_dir, original_name + ".fbx"),
                        use_selection=True,
                    )
                    bpy.ops.better_export.fbx(**export_kwargs)
                    count += 1
                except Exception as error:
                    errors.append(f"{original_name}: {error}")
                finally:
                    if temp_mesh and temp_mesh.name in bpy.data.objects:
                        temp_data = temp_mesh.data
                        bpy.data.objects.remove(temp_mesh, do_unlink=True)
                        if temp_data.users == 0:
                            bpy.data.meshes.remove(temp_data)
                    if source_mesh_renamed:
                        mesh.name = original_name
                    if source_mesh_data_renamed:
                        mesh.data.name = original_data_name
                    if temp_armature and temp_armature.name in bpy.data.objects:
                        temp_armature_data = temp_armature.data
                        bpy.data.objects.remove(temp_armature, do_unlink=True)
                        if temp_armature_data.users == 0:
                            bpy.data.armatures.remove(temp_armature_data)
        finally:
            bpy.ops.object.select_all(action="DESELECT")
            for obj in original_selected:
                if obj and obj.name in bpy.data.objects:
                    obj.select_set(True)
            if original_active and original_active.name in bpy.data.objects:
                context.view_layer.objects.active = original_active
                if (
                    original_mode != "OBJECT"
                    and original_active.mode == "OBJECT"
                    and bpy.ops.object.mode_set.poll()
                ):
                    try:
                        bpy.ops.object.mode_set(mode=original_mode)
                    except RuntimeError:
                        pass

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
    options.prop(props, "remove_unused_bones")

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
