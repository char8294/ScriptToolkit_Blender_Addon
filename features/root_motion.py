"""Create root-motion helper shapes and invert their animation constraints."""

from __future__ import annotations

import bpy
from bpy.props import IntProperty
from bpy.types import Operator


ROOT_MOTION_PREFIX = "RM_"
ROOT_MOTION_MARKER = "script_toolkit_root_motion"
ROOT_MOTION_ARMATURE = "script_toolkit_root_motion_armature"
ROOT_MOTION_BONE = "script_toolkit_root_motion_bone"
ROOT_MOTION_SHAPE = "script_toolkit_root_motion_shape"
ROOT_MOTION_CONSTRAINT_MARKER = "script_toolkit_root_motion_constraint"
ROOT_MOTION_BONE_CONSTRAINT_MARKER = "script_toolkit_root_motion_bone_constraint"

_CONSTRAINT_NAME_PREFIX = "Root Motion "

SHAPE_SPECS = {
    "Root": {
        "primitive": "CUBE",
        "dimensions": (0.16, 0.16, 0.16),
        "color": (0.0, 0.5, 1.0, 1.0),
    },
    "Pelvis": {
        "primitive": "ICO_SPHERE",
        "dimensions": (0.143, 0.15, 0.15),
    },
    "Foot.L": {
        "primitive": "CYLINDER",
        "dimensions": (0.26, 0.26, 0.031),
        "color": (1.0, 0.0, 0.0, 1.0),
    },
    "Foot.R": {
        "primitive": "CYLINDER",
        "dimensions": (0.26, 0.26, 0.031),
        "color": (1.0, 0.0, 0.0, 1.0),
    },
    "Foot Front.L": {
        "primitive": "CYLINDER",
        "dimensions": (0.26, 0.26, 0.031),
        "color": (1.0, 0.0, 0.0, 1.0),
    },
    "Foot Front.R": {
        "primitive": "CYLINDER",
        "dimensions": (0.26, 0.26, 0.031),
        "color": (1.0, 0.0, 0.0, 1.0),
    },
    "Foot Back.L": {
        "primitive": "CYLINDER",
        "dimensions": (0.26, 0.26, 0.031),
        "color": (0.0, 0.0, 1.0, 1.0),
    },
    "Foot Back.R": {
        "primitive": "CYLINDER",
        "dimensions": (0.26, 0.26, 0.031),
        "color": (0.0, 0.0, 1.0, 1.0),
    },
}


def _selected_pose_bone(context):
    """Return the one selected pose bone required by the tool."""
    armature = getattr(context, "active_object", None)
    if not armature or armature.type != "ARMATURE" or armature.mode != "POSE":
        return None, None

    selected_bones = [
        pose_bone
        for pose_bone in context.selected_pose_bones
        if pose_bone.id_data == armature
    ]
    if len(selected_bones) != 1:
        return armature, None
    return armature, selected_bones[0]


def _set_constraint_marker(constraint, marker):
    try:
        constraint[marker] = True
    except (AttributeError, TypeError, ValueError):
        # Blender versions that do not expose ID properties on constraints
        # still have the stable Root Motion name prefix as a fallback.
        pass


def _has_constraint_marker(constraint, marker):
    try:
        return bool(constraint.get(marker, False))
    except (AttributeError, TypeError, ValueError):
        return False


def _is_root_motion_constraint(constraint, marker):
    return _has_constraint_marker(constraint, marker) or constraint.name.startswith(
        _CONSTRAINT_NAME_PREFIX
    )


def _set_world_spaces(constraint):
    """Use explicit world-space defaults for both object and bone constraints."""
    constraint.owner_space = "WORLD"
    constraint.target_space = "WORLD"
    constraint.influence = 1.0


def _add_object_constraints(obj, armature, bone_name):
    copy_location = obj.constraints.new(type="COPY_LOCATION")
    copy_location.name = f"{_CONSTRAINT_NAME_PREFIX}Copy Location"
    copy_location.target = armature
    copy_location.subtarget = bone_name
    copy_location.use_x = True
    copy_location.use_y = True
    copy_location.use_z = True
    _set_world_spaces(copy_location)
    _set_constraint_marker(copy_location, ROOT_MOTION_CONSTRAINT_MARKER)

    copy_rotation = obj.constraints.new(type="COPY_ROTATION")
    copy_rotation.name = f"{_CONSTRAINT_NAME_PREFIX}Copy Rotation"
    copy_rotation.target = armature
    copy_rotation.subtarget = bone_name
    copy_rotation.use_x = True
    copy_rotation.use_y = True
    copy_rotation.use_z = True
    _set_world_spaces(copy_rotation)
    _set_constraint_marker(copy_rotation, ROOT_MOTION_CONSTRAINT_MARKER)


def _add_bone_constraints(pose_bone, obj):
    copy_location = pose_bone.constraints.new(type="COPY_LOCATION")
    copy_location.name = f"{_CONSTRAINT_NAME_PREFIX}Copy Location"
    copy_location.target = obj
    copy_location.use_x = True
    copy_location.use_y = True
    copy_location.use_z = True
    _set_world_spaces(copy_location)
    _set_constraint_marker(copy_location, ROOT_MOTION_BONE_CONSTRAINT_MARKER)

    copy_rotation = pose_bone.constraints.new(type="COPY_ROTATION")
    copy_rotation.name = f"{_CONSTRAINT_NAME_PREFIX}Copy Rotation"
    copy_rotation.target = obj
    copy_rotation.use_x = True
    copy_rotation.use_y = True
    copy_rotation.use_z = True
    _set_world_spaces(copy_rotation)
    _set_constraint_marker(copy_rotation, ROOT_MOTION_BONE_CONSTRAINT_MARKER)


def _remove_object_constraints(obj):
    removed = 0
    for constraint in list(obj.constraints):
        if constraint.type not in {"COPY_LOCATION", "COPY_ROTATION"}:
            continue
        if not _is_root_motion_constraint(
            constraint, ROOT_MOTION_CONSTRAINT_MARKER
        ):
            continue
        obj.constraints.remove(constraint)
        removed += 1
    return removed


def _root_motion_objects(context, armature, bone_name):
    return [
        obj
        for obj in context.selected_objects
        if obj.type == "MESH"
        and obj.get(ROOT_MOTION_MARKER, False)
        and obj.get(ROOT_MOTION_ARMATURE) == armature.name
        and obj.get(ROOT_MOTION_BONE) == bone_name
    ]


def _restore_context(
    context,
    selected_objects,
    active_object,
    mode,
    pose_bone_names=(),
    active_bone_name="",
):
    """Restore selection and mode after temporarily using Object Mode."""
    if context.object and context.object.mode != "OBJECT":
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in selected_objects:
        if obj and obj.name in bpy.data.objects:
            obj.select_set(True)

    if active_object and active_object.name in bpy.data.objects:
        context.view_layer.objects.active = active_object

    if (
        active_object
        and active_object.type == "ARMATURE"
        and mode != "OBJECT"
        and active_object.name in bpy.data.objects
        and bpy.ops.object.mode_set.poll()
    ):
        bpy.ops.object.mode_set(mode=mode)
        if mode == "POSE":
            for pose_bone in active_object.pose.bones:
                pose_bone.select = pose_bone.name in pose_bone_names
            if active_bone_name and active_bone_name in active_object.data.bones:
                active_object.data.bones.active = active_object.data.bones[
                    active_bone_name
                ]


def _make_primitive(context, primitive):
    if primitive == "CUBE":
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 0.0))
    elif primitive == "ICO_SPHERE":
        bpy.ops.mesh.primitive_ico_sphere_add(
            subdivisions=2,
            radius=1.0,
            location=(0.0, 0.0, 0.0),
        )
    elif primitive == "CYLINDER":
        bpy.ops.mesh.primitive_cylinder_add(
            vertices=32,
            radius=1.0,
            depth=2.0,
            location=(0.0, 0.0, 0.0),
        )
    else:
        raise ValueError(f"Unsupported root-motion primitive: {primitive}")

    obj = context.object
    if not obj or obj.type != "MESH":
        raise RuntimeError("Blender did not create a mesh shape object")
    return obj


def _create_shape_object(context, armature, bone_name, spec):
    target_collection = context.collection or context.scene.collection
    obj = _make_primitive(context, spec["primitive"])

    # Primitive operators use the active collection. Move the new object to
    # the context's current collection explicitly when one is available.
    if target_collection:
        for collection in list(obj.users_collection):
            if collection != target_collection:
                collection.objects.unlink(obj)
        if target_collection not in obj.users_collection:
            target_collection.objects.link(obj)

    obj.name = f"{ROOT_MOTION_PREFIX}{bone_name}"
    obj.location = (0.0, 0.0, 0.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.dimensions = spec["dimensions"]
    if "color" in spec:
        obj.color = spec["color"]
    obj[ROOT_MOTION_MARKER] = True
    obj[ROOT_MOTION_ARMATURE] = armature.name
    obj[ROOT_MOTION_BONE] = bone_name
    obj[ROOT_MOTION_SHAPE] = spec["primitive"]

    _add_object_constraints(obj, armature, bone_name)
    return obj


def _capture_visual_matrices(context, obj, scene, frame_start, frame_end, frame_step):
    matrices = []
    for frame in range(frame_start, frame_end + 1, frame_step):
        scene.frame_set(frame)
        context.view_layer.update()
        matrices.append((frame, obj.matrix_world.copy()))
    return matrices


def _new_baked_action(obj):
    animation_data = obj.animation_data_create()
    action = bpy.data.actions.new(f"{obj.name} Root Motion Bake")
    animation_data.action = action
    return action


def _insert_matrix_keyframes(obj, matrices):
    _new_baked_action(obj)
    for frame, matrix in matrices:
        obj.matrix_world = matrix
        obj.keyframe_insert(data_path="location", frame=frame, group="Root Motion")
        if obj.rotation_mode == "QUATERNION":
            obj.keyframe_insert(
                data_path="rotation_quaternion", frame=frame, group="Root Motion"
            )
        elif obj.rotation_mode == "AXIS_ANGLE":
            obj.keyframe_insert(
                data_path="rotation_axis_angle", frame=frame, group="Root Motion"
            )
        else:
            obj.keyframe_insert(
                data_path="rotation_euler", frame=frame, group="Root Motion"
            )


class ST_OT_CreateRootMotionShape(Operator):
    bl_idname = "script_toolkit.create_root_motion_shape"
    bl_label = "Create Shape Object"
    bl_description = (
        "Create one Root Motion shape at the Blender origin for the selected bone"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        armature, _pose_bone = _selected_pose_bone(context)
        return bool(armature)

    def execute(self, context):
        armature, pose_bone = _selected_pose_bone(context)
        if not armature:
            self.report({"ERROR"}, "Select an armature in Pose Mode first.")
            return {"CANCELLED"}
        if not pose_bone:
            self.report({"WARNING"}, "Select exactly one pose bone.")
            return {"CANCELLED"}

        bone_name = pose_bone.name
        spec = SHAPE_SPECS.get(bone_name)
        if spec is None:
            supported = ", ".join(SHAPE_SPECS)
            self.report(
                {"WARNING"},
                f"Bone '{bone_name}' has no Root Motion shape. Supported: {supported}",
            )
            return {"CANCELLED"}

        original_selected = list(context.selected_objects)
        original_active = context.view_layer.objects.active
        original_mode = armature.mode
        pose_bone_names = [bone.name for bone in context.selected_pose_bones]
        active_bone_name = armature.data.bones.active.name if armature.data.bones.active else ""

        try:
            bpy.ops.object.mode_set(mode="OBJECT")
            obj = _create_shape_object(context, armature, bone_name, spec)

            bpy.ops.object.select_all(action="DESELECT")
            armature.select_set(True)
            obj.select_set(True)
            context.view_layer.objects.active = armature
            bpy.ops.object.mode_set(mode="POSE")
            for selected_pose_bone in armature.pose.bones:
                selected_pose_bone.select = selected_pose_bone.name == bone_name
            armature.data.bones.active = armature.data.bones[bone_name]
        except Exception as error:
            self.report({"ERROR"}, f"Could not create Root Motion shape: {error}")
            _restore_context(
                context,
                original_selected,
                original_active,
                original_mode,
                pose_bone_names,
                active_bone_name,
            )
            return {"CANCELLED"}

        self.report({"INFO"}, f"Created '{obj.name}' for bone '{bone_name}'.")
        return {"FINISHED"}


class ST_OT_BakeRootMotion(Operator):
    bl_idname = "script_toolkit.bake_root_motion"
    bl_label = "Bake Animation"
    bl_description = (
        "Bake selected Root Motion objects over the current timeline, then make the bone follow them"
    )
    bl_options = {"REGISTER", "UNDO"}

    frame_start: IntProperty(name="Start Frame", default=0, min=0)
    frame_end: IntProperty(name="End Frame", default=0, min=0)
    frame_step: IntProperty(name="Frame Step", default=1, min=1)

    @classmethod
    def poll(cls, context):
        armature, _pose_bone = _selected_pose_bone(context)
        return bool(armature)

    def invoke(self, context, event):
        del event
        self.frame_start = context.scene.frame_start
        self.frame_end = context.scene.frame_end
        self.frame_step = 1
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        del context
        layout = self.layout
        layout.prop(self, "frame_start")
        layout.prop(self, "frame_end")
        layout.prop(self, "frame_step")
        layout.separator()
        layout.label(text="Only Selected Bone and Object", icon="RESTRICT_SELECT_OFF")
        layout.label(text="Visual Keying: Enabled", icon="KEYINGSET")
        layout.label(text="Bake Data: Object | Channels: Location, Rotation")
        layout.label(text="After Bake: Bone copies the baked Object", icon="CONSTRAINT")

    def execute(self, context):
        armature, pose_bone = _selected_pose_bone(context)
        if not armature:
            self.report({"ERROR"}, "Select an armature in Pose Mode first.")
            return {"CANCELLED"}
        if not pose_bone:
            self.report({"WARNING"}, "Select exactly one pose bone.")
            return {"CANCELLED"}

        scene = context.scene
        frame_start = self.frame_start or scene.frame_start
        frame_end = self.frame_end or scene.frame_end
        frame_step = self.frame_step or 1
        if frame_end < frame_start:
            self.report({"ERROR"}, "End Frame must be greater than or equal to Start Frame.")
            return {"CANCELLED"}

        objects = _root_motion_objects(context, armature, pose_bone.name)
        if not objects:
            self.report(
                {"WARNING"},
                "Select the Root Motion object that belongs to the selected bone.",
            )
            return {"CANCELLED"}
        if len(objects) != 1:
            self.report(
                {"WARNING"},
                "Select exactly one Root Motion object for the selected bone.",
            )
            return {"CANCELLED"}

        for obj in objects:
            if not any(
                constraint.type in {"COPY_LOCATION", "COPY_ROTATION"}
                and _is_root_motion_constraint(
                    constraint, ROOT_MOTION_CONSTRAINT_MARKER
                )
                for constraint in obj.constraints
            ):
                self.report(
                    {"WARNING"},
                    f"'{obj.name}' has no active Root Motion Copy constraint to bake.",
                )
                return {"CANCELLED"}

        original_selected = list(context.selected_objects)
        original_active = context.view_layer.objects.active
        original_mode = armature.mode
        pose_bone_names = [bone.name for bone in context.selected_pose_bones]
        active_bone_name = armature.data.bones.active.name if armature.data.bones.active else ""
        original_frame = scene.frame_current

        try:
            bpy.ops.object.mode_set(mode="OBJECT")

            baked_matrices = {
                obj: _capture_visual_matrices(
                    context,
                    obj,
                    scene,
                    frame_start,
                    frame_end,
                    frame_step,
                )
                for obj in objects
            }

            for obj, matrices in baked_matrices.items():
                _remove_object_constraints(obj)
                _insert_matrix_keyframes(obj, matrices)

            for obj in objects:
                _add_bone_constraints(pose_bone, obj)

            scene.frame_set(original_frame)
            context.view_layer.update()
        except Exception as error:
            self.report({"ERROR"}, f"Could not bake Root Motion: {error}")
            scene.frame_set(original_frame)
            _restore_context(
                context,
                original_selected,
                original_active,
                original_mode,
                pose_bone_names,
                active_bone_name,
            )
            return {"CANCELLED"}

        _restore_context(
            context,
            original_selected,
            original_active,
            original_mode,
            pose_bone_names,
            active_bone_name,
        )
        self.report(
            {"INFO"},
            f"Baked {len(objects)} Root Motion object(s), frames {frame_start}-{frame_end}.",
        )
        return {"FINISHED"}


def draw_ui(layout, context):
    """Draw Root Motion controls inside Script Toolkit's main panel."""
    box = layout.box()
    box.label(text="Create Root Motion Shape", icon="MESH_DATA")

    armature, pose_bone = _selected_pose_bone(context)
    if armature and pose_bone:
        box.label(text=f"Selected Bone: {pose_bone.name}", icon="BONE_DATA")
        spec = SHAPE_SPECS.get(pose_bone.name)
        if spec:
            box.label(text=f"Shape: {spec['primitive']}")
        else:
            box.label(text="No shape mapping for this bone", icon="ERROR")
    elif armature:
        box.label(text="Select exactly one pose bone", icon="INFO")
    else:
        box.label(text="Select an armature in Pose Mode", icon="INFO")
    box.operator(
        ST_OT_CreateRootMotionShape.bl_idname,
        text="Create Shape Object",
        icon="MESH_DATA",
    )

    bake_box = layout.box()
    bake_box.label(text="Bake Root Motion", icon="ACTION")
    bake_box.label(
        text=f"Timeline: {context.scene.frame_start} - {context.scene.frame_end} | Step: 1",
        icon="TIME",
    )
    bake_box.label(
        text="Select one bone and its RM_ object before baking.",
        icon="INFO",
    )
    bake_box.operator(
        ST_OT_BakeRootMotion.bl_idname,
        text="Bake Animation",
        icon="ACTION",
    )


classes = (
    ST_OT_CreateRootMotionShape,
    ST_OT_BakeRootMotion,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
