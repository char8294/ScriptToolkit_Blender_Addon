import bpy
import math
from bpy_extras.io_utils import axis_conversion
from bpy.types import Operator
from mathutils import Matrix, Vector


BONE_AXIS_ITEMS = (
    ('X', "X Axis", "X axis"),
    ('Y', "Y Axis", "Y axis"),
    ('Z', "Z Axis", "Z axis"),
    ('-X', "-X Axis", "Negative X axis"),
    ('-Y', "-Y Axis", "Negative Y axis"),
    ('-Z', "-Z Axis", "Negative Z axis"),
)

ALIGN_BONE_MODE_ITEMS = (
    (
        'SNAP',
        "Snap to Nearest Head",
        "Move each selected bone's tail to the nearest head of another selected bone",
    ),
    (
        'WORLD_AXIS',
        "Point Along World Axis",
        "Point each selected bone's tail along the chosen World Axis without snapping",
    ),
)

JTS_EX_BONE_NAME = "jts ex"
CONNECT_BONE_NAME = "connect"


def _world_axis_vector(axis):
    return {
        'X': Vector((1.0, 0.0, 0.0)),
        'Y': Vector((0.0, 1.0, 0.0)),
        'Z': Vector((0.0, 0.0, 1.0)),
        '-X': Vector((-1.0, 0.0, 0.0)),
        '-Y': Vector((0.0, -1.0, 0.0)),
        '-Z': Vector((0.0, 0.0, -1.0)),
    }.get(axis, Vector((0.0, 0.0, 1.0)))


def _world_axis_in_armature_space(armature_object, axis):
    world_direction = _world_axis_vector(axis)
    local_direction = armature_object.matrix_world.to_3x3().inverted_safe() @ world_direction
    return local_direction.normalized()


def _axis_pair_is_valid(primary, secondary):
    return primary.lstrip('-') != secondary.lstrip('-')


def bone_axis_matrix(primary, secondary):
    """Build the same axis correction matrix used by Blender's FBX importer."""
    if not _axis_pair_is_valid(primary, secondary):
        raise ValueError("Primary and Secondary Bone Axis must use different axes")

    return axis_conversion(
        from_forward='X',
        from_up='Y',
        to_forward=secondary,
        to_up=primary,
    ).to_4x4()


def _bone_depth(pose_bone):
    depth = 0
    parent = pose_bone.parent
    while parent is not None:
        depth += 1
        parent = parent.parent
    return depth


def _matrices_equal(first, second, tolerance=1e-5):
    return all(
        abs(first[row][column] - second[row][column]) <= tolerance
        for row in range(4)
        for column in range(4)
    )


def _restore_pose_matrices(armature_object, pose_matrices):
    """Apply compensated evaluated pose matrices in parent-first order."""
    for pose_bone in sorted(armature_object.pose.bones, key=_bone_depth):
        matrix = pose_matrices.get(pose_bone.name)
        if matrix is not None:
            pose_bone.matrix = matrix


def _keyframe_current_pose_if_animated(armature_object, frame, bone_names):
    """Keep the compensated pose for affected bones in the active action."""
    animation_data = armature_object.animation_data
    if animation_data is None or animation_data.action is None:
        return False

    for pose_bone in armature_object.pose.bones:
        if pose_bone.name not in bone_names:
            continue
        pose_bone.keyframe_insert(data_path="location", frame=frame)
        if pose_bone.rotation_mode == 'QUATERNION':
            pose_bone.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        elif pose_bone.rotation_mode == 'AXIS_ANGLE':
            pose_bone.keyframe_insert(data_path="rotation_axis_angle", frame=frame)
        else:
            pose_bone.keyframe_insert(data_path="rotation_euler", frame=frame)
        pose_bone.keyframe_insert(data_path="scale", frame=frame)
    return True


def _delete_matching_bones(context, keyword):
    if context.mode != 'EDIT_ARMATURE':
        bpy.ops.object.mode_set(mode='EDIT')

    edit_bones = context.active_object.data.edit_bones
    normalized_keyword = keyword.casefold()
    matching_bones = [
        bone for bone in edit_bones
        if normalized_keyword in bone.name.casefold()
    ]

    deleted_names = [bone.name for bone in matching_bones]
    for bone in matching_bones:
        edit_bones.remove(bone)
    return deleted_names


def _show_deleted_bones_report(context, keyword, deleted_names):
    if bpy.app.background:
        return

    def draw_report(menu, _context):
        layout = menu.layout
        layout.label(
            text=f"Deleted {len(deleted_names)} bone(s) matching '{keyword}':",
            icon='TRASH',
        )
        box = layout.box()
        max_display = 20
        for name in deleted_names[:max_display]:
            box.label(text=name, icon='BONE_DATA')
        if len(deleted_names) > max_display:
            box.label(text=f"...and {len(deleted_names) - max_display} more")

    context.window_manager.popup_menu(
        draw_report,
        title="Deleted Bones Report",
        icon='CHECKMARK',
    )


def _delete_bones_by_keyword(operator, context, keyword):
    deleted_names = _delete_matching_bones(context, keyword)
    if not deleted_names:
        operator.report({'WARNING'}, f"No bones matching '{keyword}' were found.")
        return {'CANCELLED'}

    operator.report({'INFO'}, f"Deleted {len(deleted_names)} bone(s) matching '{keyword}'.")
    _show_deleted_bones_report(context, keyword, deleted_names)
    return {'FINISHED'}


class ST_OT_ConvertBoneAxes(Operator):
    bl_idname = "script_toolkit.convert_bone_axes"
    bl_label = "Convert Bone Axes"
    bl_description = (
        "Convert selected bones between FBX-style Primary/Secondary axis conventions "
        "while preserving the current pose"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            context.active_object
            and context.active_object.type == 'ARMATURE'
            and context.mode == 'EDIT_ARMATURE'
        )

    def execute(self, context):
        props = context.scene.script_toolkit
        armature_object = context.active_object
        edit_bones = armature_object.data.edit_bones
        selected_bones = [bone for bone in edit_bones if bone.select]

        if not selected_bones:
            self.report({'WARNING'}, "No bones selected.")
            return {'CANCELLED'}

        try:
            source_matrix = bone_axis_matrix(
                props.bone_axis_source_primary,
                props.bone_axis_source_secondary,
            )
            target_matrix = bone_axis_matrix(
                props.bone_axis_target_primary,
                props.bone_axis_target_secondary,
            )
        except ValueError as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}

        correction_matrix = source_matrix.inverted_safe() @ target_matrix
        if correction_matrix == Matrix.Identity(4):
            self.report({'INFO'}, "Source and Target Bone Axes are already identical.")
            return {'FINISHED'}

        pose_matrices = {
            pose_bone.name: pose_bone.matrix.copy()
            for pose_bone in armature_object.pose.bones
        }
        old_rest_matrices = {
            bone.name: bone.matrix.copy()
            for bone in edit_bones
        }
        current_frame = context.scene.frame_current
        selected_matrices = {
            bone.name: bone.matrix.copy()
            for bone in selected_bones
        }

        try:
            for bone_name, matrix in selected_matrices.items():
                edit_bones[bone_name].matrix = matrix @ correction_matrix

            context.view_layer.update()
            bpy.ops.object.mode_set(mode='POSE')
            new_rest_matrices = {
                bone.name: bone.matrix_local.copy()
                for bone in armature_object.data.bones
            }
            # Armature deformation is driven by pose_matrix @ rest_matrix^-1.
            # Rebuild the pose matrix so that this deformation stays unchanged
            # after the selected bones receive their new rest matrices.
            compensated_pose_matrices = {
                name: pose_matrices[name]
                @ old_rest_matrices[name].inverted_safe()
                @ new_rest_matrices[name]
                for name in pose_matrices
            }
            pose_after_edit = {
                pose_bone.name: pose_bone.matrix.copy()
                for pose_bone in armature_object.pose.bones
            }
            keyframe_pose_bones = {
                name
                for name in selected_matrices
                if not _matrices_equal(
                    pose_after_edit[name],
                    compensated_pose_matrices[name],
                )
            }
            _restore_pose_matrices(armature_object, compensated_pose_matrices)
            context.view_layer.update()
            _keyframe_current_pose_if_animated(
                armature_object,
                current_frame,
                keyframe_pose_bones,
            )
            context.view_layer.update()
            pose_preservation_failures = [
                name
                for name, matrix in compensated_pose_matrices.items()
                if not _matrices_equal(armature_object.pose.bones[name].matrix, matrix)
            ]
            bpy.ops.object.mode_set(mode='EDIT')
        except Exception:
            if context.mode != 'EDIT_ARMATURE' and bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode='EDIT')
            raise

        if pose_preservation_failures:
            preview = ", ".join(pose_preservation_failures[:5])
            if len(pose_preservation_failures) > 5:
                preview += ", ..."
            self.report(
                {'WARNING'},
                "Converted axes, but pose compensation could not fully restore "
                "some connected descendants or constrained bones: " + preview,
            )
        else:
            self.report({'INFO'}, f"Converted axes for {len(selected_bones)} selected bone(s).")
        return {'FINISHED'}

class ST_OT_AlignBones(Operator):
    bl_idname = "script_toolkit.align_bones"
    bl_label = "Align Bones to Axis"
    bl_description = (
        "Use the selected Mode to snap each selected bone's tail to another selected "
        "head or point it along a World Axis while preserving its current length"
    )
    bl_options = {'REGISTER', 'UNDO'}
    
    axis: bpy.props.StringProperty(default='Z')

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE' and context.mode == 'EDIT_ARMATURE'

    def execute(self, context):
        props = context.scene.script_toolkit
        edit_bones = context.active_object.data.edit_bones
        selected_bones = [b for b in edit_bones if b.select]
        
        if not selected_bones:
            self.report({'WARNING'}, "No bones selected.")
            return {'CANCELLED'}
            
        snapped_count = 0
        aligned_count = 0
        affected_connected_children = []

        for bone in selected_bones:
            old_tail = bone.tail.copy()
            if props.align_bone_mode == 'WORLD_AXIS':
                axis_direction = _world_axis_in_armature_space(context.active_object, self.axis)
                bone.tail = bone.head + (axis_direction * bone.length)
                aligned_count += 1
            else:
                mat3 = bone.matrix.to_3x3()
                axis_index = {'X': 0, 'Y': 1, 'Z': 2}.get(self.axis.lstrip('-'), 2)
                search_direction = mat3.col[axis_index].normalized()
                if self.axis.startswith('-'):
                    search_direction = -search_direction

                best_candidate = None
                min_dist = float('inf')

                for other_bone in selected_bones:
                    if other_bone == bone:
                        continue

                    vec_to_other = other_bone.head - bone.head
                    dist = vec_to_other.length
                    if dist < 0.0001:
                        continue

                    vec_dir = vec_to_other.normalized()
                    angle = search_direction.angle(vec_dir)

                    # Snap if within 45 degrees.
                    if angle < math.radians(45):
                        if dist < min_dist:
                            min_dist = dist
                            best_candidate = other_bone

                if best_candidate:
                    bone.tail = best_candidate.head
                    snapped_count += 1
                else:
                    bone.tail = bone.head + (search_direction * bone.length)
                    aligned_count += 1

            if (bone.tail - old_tail).length > 0.000001:
                for child in edit_bones:
                    if (
                        child.parent == bone
                        and child.use_connect
                        and child.name not in affected_connected_children
                    ):
                        affected_connected_children.append(child.name)

        if props.align_bone_mode == 'WORLD_AXIS':
            message = (
                f"Pointed {len(selected_bones)} selected bone(s) along World Axis "
                f"{self.axis}."
            )
        else:
            message = f"Snapped {snapped_count} bones, Aligned {aligned_count} bones."

        if affected_connected_children:
            preview = ", ".join(affected_connected_children[:5])
            if len(affected_connected_children) > 5:
                preview += ", ..."
            self.report(
                {'WARNING'},
                message + " Connected child head(s) also moved: " + preview,
            )
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}

class ST_OT_SnapTailToNearest(Operator):
    bl_idname = "script_toolkit.snap_tail_to_nearest"
    bl_label = "Snap Tail to Nearest Head"
    bl_description = "Snap selected bones' tails to the nearest head of any bone within radius"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE' and context.mode == 'EDIT_ARMATURE'

    def execute(self, context):
        props = context.scene.script_toolkit
        edit_bones = context.active_object.data.edit_bones
        selected_bones = [b for b in edit_bones if b.select]
        
        if not selected_bones:
            self.report({'WARNING'}, "No bones selected.")
            return {'CANCELLED'}
            
        snapped_count = 0
        
        for bone in selected_bones:
            best_candidate = None
            min_dist = props.snap_radius
            
            for other_bone in edit_bones:
                if other_bone == bone:
                    continue
                dist = (other_bone.head - bone.tail).length
                if dist <= min_dist:
                    min_dist = dist
                    best_candidate = other_bone
                    
            if best_candidate:
                bone.tail = best_candidate.head
                snapped_count += 1
                
        self.report({'INFO'}, f"Snapped {snapped_count} bones to nearest heads.")
        return {'FINISHED'}

class ST_OT_ConnectBones(Operator):
    bl_idname = "script_toolkit.connect_touching_bones"
    bl_label = "Connect Touching Bones"
    bl_description = "Automatically parent and connect bones whose head and tail are touching"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE' and context.mode == 'EDIT_ARMATURE'

    def execute(self, context):
        edit_bones = context.active_object.data.edit_bones
        selected_bones = [b for b in edit_bones if b.select]
        
        if not selected_bones:
            self.report({'WARNING'}, "No bones selected.")
            return {'CANCELLED'}
            
        connected_count = 0
        
        for child in selected_bones:
            for parent in edit_bones:
                if parent == child:
                    continue
                if (parent.tail - child.head).length < 0.0001:
                    child.parent = parent
                    child.use_connect = True
                    connected_count += 1
                    break
                    
        self.report({'INFO'}, f"Connected {connected_count} bones.")
        return {'FINISHED'}

class ST_OT_DeleteBonesByName(Operator):
    bl_idname = "script_toolkit.delete_bones_by_name"
    bl_label = "Delete Bones by Name"
    bl_description = "Delete bones in the active armature matching the specified search keyword"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        props = context.scene.script_toolkit
        keyword = props.delete_bone_keyword.strip()

        if not keyword:
            self.report({'WARNING'}, "Please enter a bone name or keyword to delete.")
            def draw_empty(menu, context):
                menu.layout.label(text="Please enter a bone name or keyword to delete.", icon='ERROR')
            context.window_manager.popup_menu(draw_empty, title="Delete Bones Report", icon='CANCEL')
            return {'CANCELLED'}

        deleted_names = _delete_matching_bones(context, keyword)

        if not deleted_names:
            self.report({'WARNING'}, f"No bones matching '{keyword}' were found.")
            def draw_not_found(menu, context):
                menu.layout.label(text=f"No bones matching '{keyword}' were found.", icon='INFO')
            context.window_manager.popup_menu(draw_not_found, title="Delete Bones Report", icon='INFO')
            return {'CANCELLED'}

        self.report({'INFO'}, f"Deleted {len(deleted_names)} bone(s) matching '{keyword}'.")
        _show_deleted_bones_report(context, keyword, deleted_names)
        return {'FINISHED'}


class ST_OT_DeleteJtsExBones(Operator):
    bl_idname = "script_toolkit.delete_jts_ex_bones"
    bl_label = "Delete JTS EX"
    bl_description = f"Delete bones whose names contain '{JTS_EX_BONE_NAME}'"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        return _delete_bones_by_keyword(self, context, JTS_EX_BONE_NAME)


class ST_OT_DeleteConnectBone(Operator):
    bl_idname = "script_toolkit.delete_connect_bone"
    bl_label = "Delete CONNECT"
    bl_description = f"Delete bones whose names contain '{CONNECT_BONE_NAME}'"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        return _delete_bones_by_keyword(self, context, CONNECT_BONE_NAME)

def draw_ui(layout, context):
    props = context.scene.script_toolkit
    
    # --- Align Bones ---
    align_box = layout.box()
    align_box.label(text="Align Bones (Edit Mode)", icon='CON_LOCLIKE')
    
    active_obj = context.active_object
    arm = active_obj.data if (active_obj and active_obj.type == 'ARMATURE') else None

    sub = align_box.column()
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
            
    align_box.separator()
    align_box.prop(props, "align_bone_mode", text="Mode")
    if props.align_bone_mode == 'WORLD_AXIS':
        align_box.label(text="Point Selected Tails Along World Axis:")
        align_box.label(text="No snapping; each Bone keeps its current length.")
    else:
        align_box.label(text="Snap Selected Tail to Nearest Other Selected Head:")
        align_box.label(text="Search follows each Bone's Local Axis; no match keeps its length.")
    
    col = align_box.column(align=True)
    
    row1 = col.row(align=True)
    op_x = row1.operator("script_toolkit.align_bones", text="+X")
    op_x.axis = 'X'
    op_nx = row1.operator("script_toolkit.align_bones", text="-X")
    op_nx.axis = '-X'
    
    row2 = col.row(align=True)
    op_y = row2.operator("script_toolkit.align_bones", text="+Y")
    op_y.axis = 'Y'
    op_ny = row2.operator("script_toolkit.align_bones", text="-Y")
    op_ny.axis = '-Y'
    
    row3 = col.row(align=True)
    op_z = row3.operator("script_toolkit.align_bones", text="+Z")
    op_z.axis = 'Z'
    op_nz = row3.operator("script_toolkit.align_bones", text="-Z")
    op_nz.axis = '-Z'

    align_box.separator()
    align_box.label(text="Convert Bone Axes (FBX Import Style):", icon='CONSTRAINT')

    source_row = align_box.row(align=True)
    source_row.label(text="Source")
    source_row.prop(props, "bone_axis_source_primary", text="Primary")
    source_row.prop(props, "bone_axis_source_secondary", text="Secondary")

    target_row = align_box.row(align=True)
    target_row.label(text="Target")
    target_row.prop(props, "bone_axis_target_primary", text="Primary")
    target_row.prop(props, "bone_axis_target_secondary", text="Secondary")

    align_box.operator("script_toolkit.convert_bone_axes", icon='CONSTRAINT')
    
    align_box.separator()
    align_box.label(text="Advanced Snapping:", icon='SNAP_ON')
    row = align_box.row()
    row.prop(props, "snap_radius")
    align_box.operator("script_toolkit.snap_tail_to_nearest", icon='SNAP_VERTEX')
    align_box.operator(
        "script_toolkit.connect_touching_bones",
        text="Connect Touching Bones",
        icon='CONSTRAINT',
    )

    align_box.separator()
    align_box.label(text="Delete Bones by Name:", icon='TRASH')
    row_del = align_box.row(align=True)
    row_del.prop(props, "delete_bone_keyword", text="", icon='VIEWZOOM')
    row_del.operator("script_toolkit.delete_bones_by_name", text="Delete", icon='X')

    quick_actions = align_box.row(align=True)
    quick_actions.operator("script_toolkit.delete_jts_ex_bones", text="Delete JTS EX", icon='X')
    quick_actions.operator("script_toolkit.delete_connect_bone", text="Delete CONNECT", icon='X')

classes = (
    ST_OT_ConvertBoneAxes,
    ST_OT_AlignBones,
    ST_OT_SnapTailToNearest,
    ST_OT_ConnectBones,
    ST_OT_DeleteBonesByName,
    ST_OT_DeleteJtsExBones,
    ST_OT_DeleteConnectBone,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
