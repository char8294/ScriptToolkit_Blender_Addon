import bpy
from bpy.types import Operator, UIList, PropertyGroup
from bpy.props import BoolProperty, StringProperty, IntProperty
from mathutils import Vector


IK_HELPER_POLE = "POLE"
IK_HELPER_MCH = "MCH_IK"
IK_HELPER_FOOT = "FOOT"


def _active_bone_name(context, armature):
    """Return the active bone name from an armature in any supported mode."""
    if not armature or armature.type != 'ARMATURE':
        return None

    if getattr(context, "active_object", None) != armature:
        return None

    mode = getattr(context, "mode", "")
    if mode == 'POSE':
        active_pose_bone = getattr(context, "active_pose_bone", None)
        if active_pose_bone:
            return active_pose_bone.name

    if mode == 'EDIT_ARMATURE':
        edit_bones = getattr(armature.data, "edit_bones", None)
        active_edit_bone = getattr(edit_bones, "active", None)
        if active_edit_bone:
            return active_edit_bone.name

    data_bones = getattr(armature.data, "bones", None)
    active_data_bone = getattr(data_bones, "active", None)
    if active_data_bone:
        return active_data_bone.name
    return None


def _active_armature_bone(context):
    """Return the active armature and active bone name for IK helper actions."""
    armature = getattr(context, "active_object", None)
    return armature, _active_bone_name(context, armature)


def _selected_pose_bone_pair(operator, context, role):
    """Return (armature, selected_role_name, active_ik_name) from two pose bones."""
    armature = getattr(context, "active_object", None)
    if not armature or armature.type != 'ARMATURE' or armature.mode != 'POSE':
        operator.report({'ERROR'}, "Select two bones in Pose Mode on one armature.")
        return None

    selected_armatures = [
        obj
        for obj in getattr(context, "selected_objects", ())
        if obj.type == 'ARMATURE'
    ]
    if len(selected_armatures) != 1 or selected_armatures[0] != armature:
        operator.report(
            {'WARNING'},
            "Select the two bones on one armature only; multi-armature selection is not supported.",
        )
        return None

    selected_pose_bones = [
        pose_bone
        for pose_bone in armature.pose.bones
        if getattr(pose_bone, "select", False)
    ]
    if len(selected_pose_bones) != 2:
        operator.report(
            {'WARNING'},
            f"Select exactly two bones: {role} first and the IK bone active last.",
        )
        return None

    active_pose_bone = getattr(context, "active_pose_bone", None)
    if not active_pose_bone:
        active_data_bone = getattr(armature.data.bones, "active", None)
        active_pose_bone = (
            armature.pose.bones.get(active_data_bone.name)
            if active_data_bone
            else None
        )
    if not active_pose_bone or not active_pose_bone.select:
        operator.report({'WARNING'}, "The IK bone must be the active selected bone.")
        return None

    selected_role_bones = [
        pose_bone
        for pose_bone in selected_pose_bones
        if pose_bone.name != active_pose_bone.name
    ]
    if len(selected_role_bones) != 1:
        operator.report({'WARNING'}, "Could not identify the selected role bone.")
        return None

    return armature, selected_role_bones[0].name, active_pose_bone.name


def _ik_constraints(pose_bone):
    return [constraint for constraint in pose_bone.constraints if constraint.type == 'IK']


def _global_y_direction_in_armature_space(armature, sign):
    """Convert a global Y direction into a normalized armature-space vector."""
    world_direction = Vector((0.0, float(sign), 0.0))
    armature_direction = armature.matrix_world.to_3x3().inverted_safe() @ world_direction
    if armature_direction.length_squared == 0.0:
        raise ValueError("The armature transform cannot represent a global Y direction")
    return armature_direction.normalized()


def _restore_armature_mode(armature, original_mode):
    if original_mode == 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')
    elif original_mode == 'POSE':
        bpy.ops.object.mode_set(mode='POSE')


def _selected_bone_names(armature, mode):
    if mode == 'EDIT':
        return {
            bone.name
            for bone in armature.data.edit_bones
            if getattr(bone, "select", False)
        }
    if mode == 'POSE':
        return {
            pose_bone.name
            for pose_bone in armature.pose.bones
            if getattr(pose_bone, "select", False)
        }
    return set()


def _restore_bone_selection(armature, bone_name, mode, selected_names):
    """Restore the source bone as active after the helper is created."""
    data_bone = armature.data.bones.get(bone_name)
    if not data_bone:
        return

    armature.data.bones.active = data_bone
    if mode == 'EDIT':
        for bone in armature.data.edit_bones:
            bone.select = bone.name in selected_names or bone.name == bone_name
    elif mode == 'POSE':
        for pose_bone in armature.pose.bones:
            pose_bone.select = (
                pose_bone.name in selected_names or pose_bone.name == bone_name
            )


def _create_ik_helper_bone(operator, context, helper_type):
    """Create one independent IK helper bone from the current active bone."""
    armature, source_name = _active_armature_bone(context)
    if not armature or armature.type != 'ARMATURE':
        operator.report({'ERROR'}, "Select an armature and an active bone first.")
        return {'CANCELLED'}
    if not source_name or source_name not in armature.data.bones:
        operator.report({'ERROR'}, "Select an active bone first.")
        return {'CANCELLED'}

    props = context.scene.script_toolkit
    default_name_property = {
        IK_HELPER_POLE: "ik_pole_name",
        IK_HELPER_MCH: "ik_mch_ik_name",
        IK_HELPER_FOOT: "ik_foot_name",
    }[helper_type]
    helper_name = getattr(operator, "target_name", "").strip()
    if not helper_name:
        helper_name = getattr(props, default_name_property, "").strip()
    if not helper_name:
        operator.report({'ERROR'}, "The new bone name cannot be empty.")
        return {'CANCELLED'}
    if helper_name in armature.data.bones:
        operator.report({'WARNING'}, f"Bone '{helper_name}' already exists; nothing was created.")
        return {'CANCELLED'}

    bone_length = float(props.ik_helper_bone_length)
    pole_distance = float(props.ik_pole_distance)
    if bone_length <= 0.0:
        operator.report({'ERROR'}, "Bone Length must be greater than zero.")
        return {'CANCELLED'}
    if pole_distance < 0.0:
        operator.report({'ERROR'}, "Pole Distance cannot be negative.")
        return {'CANCELLED'}

    original_mode = armature.mode
    selected_bone_names = _selected_bone_names(armature, original_mode)
    created_name = None
    try:
        if context.active_object != armature:
            bpy.ops.object.select_all(action='DESELECT')
            armature.select_set(True)
            context.view_layer.objects.active = armature

        if armature.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.mode_set(mode='EDIT')

        edit_bones = armature.data.edit_bones
        source_bone = edit_bones.get(source_name)
        if not source_bone:
            raise ValueError(f"Active bone '{source_name}' is no longer available")

        if helper_name in edit_bones:
            operator.report({'WARNING'}, f"Bone '{helper_name}' already exists; nothing was created.")
            bpy.ops.object.mode_set(mode='OBJECT')
            _restore_armature_mode(armature, original_mode)
            return {'CANCELLED'}

        if helper_type == IK_HELPER_POLE:
            direction = _global_y_direction_in_armature_space(armature, -1.0)
        else:
            direction = _global_y_direction_in_armature_space(armature, 1.0)

        source_head = source_bone.head.copy()
        new_bone = edit_bones.new(helper_name)
        new_bone.head = source_head
        new_bone.tail = source_head + direction * bone_length
        new_bone.parent = None
        new_bone.use_connect = False

        if helper_type == IK_HELPER_POLE:
            # Match the requested sequence: extrude from the source head,
            # move the new bone farther along global -Y, then reverse it.
            offset = direction * pole_distance
            moved_head = new_bone.head + offset
            moved_tail = new_bone.tail + offset
            new_bone.head = moved_tail
            new_bone.tail = moved_head

        created_name = new_bone.name
        bpy.ops.object.mode_set(mode='OBJECT')

        if helper_type == IK_HELPER_POLE:
            pose_bone = armature.pose.bones.get(created_name)
            if not pose_bone:
                raise ValueError(f"Created bone '{created_name}' has no pose bone")
            constraint = pose_bone.constraints.new(type='DAMPED_TRACK')
            constraint.name = f"Damped Track to {source_name}"
            constraint.target = armature
            constraint.subtarget = source_name
            constraint.track_axis = 'TRACK_Y'

        _restore_armature_mode(armature, original_mode)
        _restore_bone_selection(
            armature,
            source_name,
            original_mode,
            selected_bone_names,
        )

    except Exception as error:
        if armature.mode == 'EDIT':
            if created_name:
                created_bone = armature.data.edit_bones.get(created_name)
                if created_bone:
                    armature.data.edit_bones.remove(created_bone)
            bpy.ops.object.mode_set(mode='OBJECT')
        if armature.mode != original_mode:
            _restore_armature_mode(armature, original_mode)
        operator.report({'ERROR'}, f"Could not create '{helper_name}': {error}")
        return {'CANCELLED'}

    operator.report({'INFO'}, f"Created '{created_name}' from active bone '{source_name}'.")
    return {'FINISHED'}

class ST_BoneHierarchyItem(PropertyGroup):
    name: StringProperty()
    indent: IntProperty(default=0)

class ST_UL_BoneHierarchy(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row()
            if item.indent > 0:
                # Add indentation visually
                split = row.split(factor=item.indent * 0.05)
                split.label(text="")
                row = split.row()
            
            row.label(text=item.name, icon='BONE_DATA')
        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            layout.label(text="", icon='BONE_DATA')

class ST_OT_RefreshBoneHierarchy(Operator):
    bl_idname = "script_toolkit.refresh_bone_hierarchy"
    bl_label = "Refresh Hierarchy"
    bl_description = "Refresh the bone hierarchy list from the selected target armature"
    
    @classmethod
    def poll(cls, context):
        props = context.scene.script_toolkit
        return props.target_armature is not None

    def execute(self, context):
        props = context.scene.script_toolkit
        armature = props.target_armature
        
        props.bone_hierarchy.clear()
        
        if not armature or armature.type != 'ARMATURE':
            return {'CANCELLED'}
        
        def add_bone_recursive(bone, indent_level):
            item = props.bone_hierarchy.add()
            item.name = bone.name
            item.indent = indent_level
            for child in bone.children:
                add_bone_recursive(child, indent_level + 1)

        # Build hierarchy starting from root bones
        for bone in armature.data.bones:
            if bone.parent is None:
                add_bone_recursive(bone, 0)
        
        return {'FINISHED'}

class ST_OT_PickTargetArmature(Operator):
    bl_idname = "script_toolkit.pick_target_armature"
    bl_label = "Pick Selected Armature"
    bl_description = "Set the Target Armature to the currently active Armature"

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        props = context.scene.script_toolkit
        props.target_armature = context.active_object
        return {'FINISHED'}

class ST_OT_EmptyToBone(Operator):
    bl_idname = "script_toolkit.empty_to_bone"
    bl_label = "Convert to Bone"
    bl_description = (
        "Convert selected Empties and Armature origins to Bones in the target Armature"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.script_toolkit
        return props.target_armature is not None

    def execute(self, context):
        props = context.scene.script_toolkit
        armature = props.target_armature
        source_objects = [
            obj for obj in context.selected_objects
            if obj.type in {'EMPTY', 'ARMATURE'}
        ]
        
        if not source_objects:
            self.report({'WARNING'}, "No Empty or Armature objects selected.")
            return {'CANCELLED'}
            
        if armature.type != 'ARMATURE':
            self.report({'ERROR'}, "Target is not an Armature.")
            return {'CANCELLED'}

        # Find the parent bone name from the UIList
        parent_bone_name = None
        if len(props.bone_hierarchy) > 0 and 0 <= props.bone_hierarchy_index < len(props.bone_hierarchy):
            parent_bone_name = props.bone_hierarchy[props.bone_hierarchy_index].name

        # Ensure we are in Object Mode to start clean
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Select the target armature and make it active
        bpy.ops.object.select_all(action='DESELECT')
        armature.select_set(True)
        context.view_layer.objects.active = armature

        # Enter Edit Mode
        bpy.ops.object.mode_set(mode='EDIT')
        edit_bones = armature.data.edit_bones

        parent_bone = None
        if parent_bone_name and parent_bone_name in edit_bones:
            parent_bone = edit_bones[parent_bone_name]

        armature_matrix_inv = armature.matrix_world.inverted()

        for source_object in source_objects:
            # Calculate position in armature space
            armature_space_matrix = armature_matrix_inv @ source_object.matrix_world
            head_pos = armature_space_matrix.translation
            
            # The source object's Y axis in armature space
            y_axis = (armature_space_matrix.to_3x3() @ Vector((0, 1, 0))).normalized()
            tail_pos = head_pos + (y_axis * props.bone_length)

            # Create new bone
            new_bone = edit_bones.new(source_object.name)
            new_bone.head = head_pos
            new_bone.tail = tail_pos
            
            if parent_bone:
                if props.bone_relation == 'CHILD':
                    new_bone.parent = parent_bone
                elif props.bone_relation == 'PARENT':
                    new_bone.parent = parent_bone.parent
                    parent_bone.parent = new_bone

        # Return to Object Mode
        bpy.ops.object.mode_set(mode='OBJECT')
        
        # Reselect source objects so the user doesn't lose the selection.
        bpy.ops.object.select_all(action='DESELECT')
        for source_object in source_objects:
            source_object.select_set(True)
        context.view_layer.objects.active = source_objects[0]

        self.report(
            {'INFO'},
            f"Created {len(source_objects)} bones in '{armature.name}'.",
        )
        return {'FINISHED'}


class ST_OT_CreatePoleBone(Operator):
    bl_idname = "script_toolkit.create_pole_bone"
    bl_label = "Create Pole"
    bl_description = (
        "Create an unparented Pole bone from the active bone and add a Y-axis Damped Track"
    )
    bl_options = {'REGISTER', 'UNDO'}
    target_name: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        armature, bone_name = _active_armature_bone(context)
        return bool(armature and bone_name)

    def execute(self, context):
        return _create_ik_helper_bone(self, context, IK_HELPER_POLE)


class ST_OT_CreateMCHIKBone(Operator):
    bl_idname = "script_toolkit.create_mch_ik_bone"
    bl_label = "Create MCH-IK"
    bl_description = "Create an unparented MCH-IK bone from the active bone along global +Y"
    bl_options = {'REGISTER', 'UNDO'}
    target_name: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        armature, bone_name = _active_armature_bone(context)
        return bool(armature and bone_name)

    def execute(self, context):
        return _create_ik_helper_bone(self, context, IK_HELPER_MCH)


class ST_OT_CreateFootBone(Operator):
    bl_idname = "script_toolkit.create_foot_bone"
    bl_label = "Create Foot"
    bl_description = "Create an unparented Foot bone from the active bone along global +Y"
    bl_options = {'REGISTER', 'UNDO'}
    target_name: StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        armature, bone_name = _active_armature_bone(context)
        return bool(armature and bone_name)

    def execute(self, context):
        return _create_ik_helper_bone(self, context, IK_HELPER_FOOT)


class ST_OT_CreateIKTarget(Operator):
    bl_idname = "script_toolkit.create_ik_target"
    bl_label = "IK Target"
    bl_description = (
        "Create an IK constraint: select the Target first and the IK bone active last"
    )
    bl_options = {'REGISTER', 'UNDO'}
    confirm_duplicate: BoolProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(
            getattr(context, "active_object", None)
            and getattr(context.active_object, "type", None) == 'ARMATURE'
            and getattr(context.active_object, "mode", None) == 'POSE'
        )

    def _selection(self, context):
        return _selected_pose_bone_pair(self, context, "Target")

    def invoke(self, context, event):
        selection = self._selection(context)
        if not selection:
            return {'CANCELLED'}

        armature, _target_name, ik_name = selection
        if _ik_constraints(armature.pose.bones[ik_name]):
            self.confirm_duplicate = True
            return context.window_manager.invoke_props_dialog(self, width=360)
        return self.execute(context)

    def draw(self, context):
        if self.confirm_duplicate:
            self.layout.label(
                text="An IK Constraint already exists. Create another?",
                icon='QUESTION',
            )

    def execute(self, context):
        selection = self._selection(context)
        if not selection:
            return {'CANCELLED'}

        armature, target_name, ik_name = selection
        ik_pose_bone = armature.pose.bones[ik_name]
        if _ik_constraints(ik_pose_bone) and not self.confirm_duplicate:
            self.report(
                {'WARNING'},
                "An IK Constraint already exists; confirm before creating another.",
            )
            return {'CANCELLED'}

        constraint = ik_pose_bone.constraints.new(type='IK')
        constraint.name = "IK Target"
        constraint.target = armature
        constraint.subtarget = target_name
        constraint.chain_count = 2
        constraint.pole_angle = 0.0
        self.report(
            {'INFO'},
            f"Created IK on '{ik_name}' targeting '{target_name}' with chain length 2.",
        )
        return {'FINISHED'}


class ST_OT_SetPoleTarget(Operator):
    bl_idname = "script_toolkit.set_pole_target"
    bl_label = "Pole Target"
    bl_description = (
        "Set the Pole Target on every IK constraint: select the Pole first and the IK bone active last"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(
            getattr(context, "active_object", None)
            and getattr(context.active_object, "type", None) == 'ARMATURE'
            and getattr(context.active_object, "mode", None) == 'POSE'
        )

    def execute(self, context):
        selection = _selected_pose_bone_pair(self, context, "Pole Target")
        if not selection:
            return {'CANCELLED'}

        armature, pole_name, ik_name = selection
        ik_constraints = _ik_constraints(armature.pose.bones[ik_name])
        if not ik_constraints:
            self.report(
                {'WARNING'},
                f"No IK Constraint found on '{ik_name}'. Create IK Target first.",
            )
            return {'CANCELLED'}

        for constraint in ik_constraints:
            constraint.pole_target = armature
            constraint.pole_subtarget = pole_name

        self.report(
            {'INFO'},
            f"Set Pole Target '{pole_name}' on {len(ik_constraints)} IK constraint(s) on '{ik_name}'.",
        )
        return {'FINISHED'}



def draw_ui(layout, context):
    props = context.scene.script_toolkit
    
    # --- Empty to Bone ---
    conv_box = layout.box()
    conv_box.label(text="Empty to Bone Converter", icon='GROUP_BONE')
    
    row = conv_box.row(align=True)
    row.prop(props, "target_armature", text="Target")
    row.operator("script_toolkit.pick_target_armature", text="", icon='RESTRICT_SELECT_OFF')
    
    if props.target_armature:
        conv_box.separator()
        conv_box.prop(props, "bone_length")
        conv_box.prop(props, "bone_relation")
        
        conv_box.separator()
        row = conv_box.row()
        row.label(text="Parent Bone:", icon='BONE_DATA')
        row.operator("script_toolkit.refresh_bone_hierarchy", text="", icon='FILE_REFRESH')
        
        conv_box.template_list(
            "ST_UL_BoneHierarchy", 
            "", 
            props, 
            "bone_hierarchy", 
            props, 
            "bone_hierarchy_index",
            rows=10
        )
        
        conv_box.operator(
            "script_toolkit.empty_to_bone",
            icon='GROUP_BONE',
            text="Convert Selected Empties / Armatures to Bones",
        )

    ik_box = layout.box()
    ik_box.label(text="IK Helper Bones", icon='CONSTRAINT_BONE')
    active_armature = getattr(context, "active_object", None)
    active_bone_name = _active_bone_name(context, active_armature)
    if active_bone_name:
        ik_box.label(text=f"Active Bone: {active_bone_name}", icon='BONE_DATA')
    else:
        ik_box.label(text="Select an active bone in an armature first", icon='INFO')

    ik_box.prop(props, "ik_helper_preset", text="Preset")
    ik_box.prop(props, "ik_helper_bone_length", text="Bone Length")
    ik_box.prop(props, "ik_pole_distance", text="Pole Distance")

    if props.ik_helper_preset == "LEG_2":
        helper_rows = (
            ("ik_pole_name", "Pole:", "script_toolkit.create_pole_bone"),
            ("ik_mch_ik_name", "MCH-IK:", "script_toolkit.create_mch_ik_bone"),
            ("ik_foot_name", "Foot:", "script_toolkit.create_foot_bone"),
        )
        for property_name, label, operator_id in helper_rows:
            row = ik_box.row(align=True)
            row.prop(props, property_name, text=label)
            operator = row.operator(operator_id, text="Create", icon='BONE_DATA')
            operator.target_name = getattr(props, property_name)
    else:
        helper_rows = (
            (
                "ik_pole_front_name",
                "ik_pole_back_name",
                "script_toolkit.create_pole_bone",
            ),
            (
                "ik_mch_ik_front_name",
                "ik_mch_ik_back_name",
                "script_toolkit.create_mch_ik_bone",
            ),
            (
                "ik_foot_front_name",
                "ik_foot_back_name",
                "script_toolkit.create_foot_bone",
            ),
        )
        for front_property, back_property, operator_id in helper_rows:
            split = ik_box.split(factor=0.5)
            front_row = split.row(align=True)
            front_row.prop(props, front_property, text="Front:")
            front_operator = front_row.operator(operator_id, text="Create", icon='BONE_DATA')
            front_operator.target_name = getattr(props, front_property)

            back_row = split.row(align=True)
            back_row.prop(props, back_property, text="Back:")
            back_operator = back_row.operator(operator_id, text="Create", icon='BONE_DATA')
            back_operator.target_name = getattr(props, back_property)

    constraint_box = layout.box()
    constraint_box.label(text="IK Constraint", icon='CONSTRAINT')
    constraint_box.label(text="IK Target: select Target, then IK Bone (active last).")
    constraint_box.operator(
        "script_toolkit.create_ik_target",
        text="IK Target",
        icon='CONSTRAINT_BONE',
    )
    constraint_box.label(text="Pole Target: select Pole, then IK Bone (active last).")
    constraint_box.operator(
        "script_toolkit.set_pole_target",
        text="Pole Target",
        icon='CONSTRAINT_BONE',
    )
    constraint_box.label(text="Chain Length: 2 | Pole Angle: 0°", icon='INFO')

@bpy.app.handlers.persistent
def auto_refresh_bone_hierarchy(scene, depsgraph):
    props = getattr(scene, "script_toolkit", None)
    if not props:
        return
    
    armature = props.target_armature
    if not armature or armature.type != 'ARMATURE':
        return
    
    # Fast hash of bone hierarchy
    current_state = "".join(f"{b.name}:{b.parent.name if b.parent else ''}," for b in armature.data.bones)
    current_hash = str(hash(current_state))
    
    if props.last_hierarchy_hash == current_hash:
        return
        
    props.last_hierarchy_hash = current_hash
    
    # Rebuild
    props.bone_hierarchy.clear()
    
    def add_bone_recursive(bone, indent_level):
        item = props.bone_hierarchy.add()
        item.name = bone.name
        item.indent = indent_level
        for child in bone.children:
            add_bone_recursive(child, indent_level + 1)

    for bone in armature.data.bones:
        if bone.parent is None:
            add_bone_recursive(bone, 0)

classes = (
    ST_BoneHierarchyItem,
    ST_UL_BoneHierarchy,
    ST_OT_RefreshBoneHierarchy,
    ST_OT_PickTargetArmature,
    ST_OT_EmptyToBone,
    ST_OT_CreatePoleBone,
    ST_OT_CreateMCHIKBone,
    ST_OT_CreateFootBone,
    ST_OT_CreateIKTarget,
    ST_OT_SetPoleTarget,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if auto_refresh_bone_hierarchy not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(auto_refresh_bone_hierarchy)

def unregister():
    if auto_refresh_bone_hierarchy in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(auto_refresh_bone_hierarchy)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
