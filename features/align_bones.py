import bpy
import math
from bpy.types import Operator

class ST_OT_AlignBones(Operator):
    bl_idname = "script_toolkit.align_bones"
    bl_label = "Align Bones to Axis"
    bl_description = "Align selected bones' tails along the chosen local axis"
    bl_options = {'REGISTER', 'UNDO'}
    
    axis: bpy.props.StringProperty(default='Z')

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE' and context.mode == 'EDIT_ARMATURE'

    def execute(self, context):
        edit_bones = context.active_object.data.edit_bones
        selected_bones = [b for b in edit_bones if b.select]
        
        if not selected_bones:
            self.report({'WARNING'}, "No bones selected.")
            return {'CANCELLED'}
            
        snapped_count = 0
        aligned_count = 0
            
        for bone in selected_bones:
            mat3 = bone.matrix.to_3x3()
            
            if self.axis == 'X':
                search_dir = mat3.col[0].normalized()
            elif self.axis == 'Y':
                search_dir = mat3.col[1].normalized()
            elif self.axis == 'Z':
                search_dir = mat3.col[2].normalized()
            elif self.axis == '-X':
                search_dir = -mat3.col[0].normalized()
            elif self.axis == '-Y':
                search_dir = -mat3.col[1].normalized()
            elif self.axis == '-Z':
                search_dir = -mat3.col[2].normalized()
            else:
                search_dir = mat3.col[2].normalized()
                
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
                angle = search_dir.angle(vec_dir)
                
                # Snap if within 45 degrees
                if angle < math.radians(45):
                    if dist < min_dist:
                        min_dist = dist
                        best_candidate = other_bone
                        
            if best_candidate:
                bone.tail = best_candidate.head
                snapped_count += 1
            else:
                bone.tail = bone.head + (search_dir * bone.length)
                aligned_count += 1
            
        self.report({'INFO'}, f"Snapped {snapped_count} bones, Aligned {aligned_count} bones.")
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

        if context.mode != 'EDIT_ARMATURE':
            bpy.ops.object.mode_set(mode='EDIT')

        edit_bones = context.active_object.data.edit_bones
        kw_lower = keyword.lower()
        matching_bones = [b for b in edit_bones if kw_lower in b.name.lower()]

        if not matching_bones:
            self.report({'WARNING'}, f"No bones matching '{keyword}' were found.")
            def draw_not_found(menu, context):
                menu.layout.label(text=f"No bones matching '{keyword}' were found.", icon='INFO')
            context.window_manager.popup_menu(draw_not_found, title="Delete Bones Report", icon='INFO')
            return {'CANCELLED'}

        deleted_names = [b.name for b in matching_bones]
        for bone in matching_bones:
            edit_bones.remove(bone)

        self.report({'INFO'}, f"Deleted {len(deleted_names)} bone(s) matching '{keyword}'.")

        def draw_report(menu, context):
            layout = menu.layout
            layout.label(text=f"Deleted {len(deleted_names)} bone(s) matching '{keyword}':", icon='TRASH')
            box = layout.box()
            max_display = 20
            for name in deleted_names[:max_display]:
                box.label(text=name, icon='BONE_DATA')
            if len(deleted_names) > max_display:
                box.label(text=f"...and {len(deleted_names) - max_display} more")

        context.window_manager.popup_menu(draw_report, title="Deleted Bones Report", icon='CHECKMARK')
        return {'FINISHED'}

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
    align_box.label(text="Align to Local Axis (Snap to Nearest):")
    
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
    align_box.label(text="Advanced Snapping:", icon='SNAP_ON')
    row = align_box.row()
    row.prop(props, "snap_radius")
    align_box.operator("script_toolkit.snap_tail_to_nearest", icon='SNAP_VERTEX')
    align_box.operator("script_toolkit.connect_touching_bones", icon='CONSTRAINT')

    align_box.separator()
    align_box.label(text="Delete Bones by Name:", icon='TRASH')
    row_del = align_box.row(align=True)
    row_del.prop(props, "delete_bone_keyword", text="", icon='VIEWZOOM')
    row_del.operator("script_toolkit.delete_bones_by_name", text="Delete", icon='X')

classes = (
    ST_OT_AlignBones,
    ST_OT_SnapTailToNearest,
    ST_OT_ConnectBones,
    ST_OT_DeleteBonesByName,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
