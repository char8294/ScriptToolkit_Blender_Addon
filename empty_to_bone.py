import bpy
from bpy.types import Operator, UIList, PropertyGroup
from bpy.props import StringProperty, IntProperty
from mathutils import Vector

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
    bl_description = "Convert selected Empties to Bones in the target Armature"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.script_toolkit
        return props.target_armature is not None

    def execute(self, context):
        props = context.scene.script_toolkit
        armature = props.target_armature
        empties = [obj for obj in context.selected_objects if obj.type == 'EMPTY']
        
        if not empties:
            self.report({'WARNING'}, "No Empty objects selected.")
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

        for empty in empties:
            # Calculate position in armature space
            armature_space_matrix = armature_matrix_inv @ empty.matrix_world
            head_pos = armature_space_matrix.translation
            
            # The Empty's Y axis in armature space
            y_axis = (armature_space_matrix.to_3x3() @ Vector((0, 1, 0))).normalized()
            tail_pos = head_pos + (y_axis * props.bone_length)

            # Create new bone
            new_bone = edit_bones.new(empty.name)
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
        
        # Reselect empties so user doesn't lose selection
        bpy.ops.object.select_all(action='DESELECT')
        for empty in empties:
            empty.select_set(True)
        context.view_layer.objects.active = empties[0] if empties else None

        self.report({'INFO'}, f"Created {len(empties)} bones in '{armature.name}'.")
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
        
        conv_box.operator("script_toolkit.empty_to_bone", icon='GROUP_BONE', text="Convert Selected Empties to Bones")

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
