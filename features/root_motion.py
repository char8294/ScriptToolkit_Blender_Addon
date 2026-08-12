"""Create Root Motion helper shapes and pair bones with RM_ objects."""

from __future__ import annotations

import re

import bpy
from bpy.props import StringProperty
from bpy.types import Operator


ROOT_MOTION_PREFIX = "RM_"

SHAPE_PRESETS = {
    "CUBE_BLUE": {
        "shape": "CUBE",
        "primitive": "CUBE",
        "color_name": "Blue",
        "dimensions": (0.16, 0.16, 0.16),
        "color": (0.0, 0.5, 1.0, 1.0),
    },
    "ICO_SPHERE_YELLOW": {
        "shape": "ICO_SPHERE",
        "primitive": "ICO_SPHERE",
        "color_name": "Yellow",
        "dimensions": (0.143, 0.15, 0.15),
        "color": (1.0, 1.0, 0.0, 1.0),
    },
    "CYLINDER_RED": {
        "shape": "CYLINDER",
        "primitive": "CYLINDER",
        "color_name": "Red",
        "dimensions": (0.26, 0.26, 0.031),
        "color": (1.0, 0.0, 0.0, 1.0),
    },
    "CYLINDER_BLUE": {
        "shape": "CYLINDER",
        "primitive": "CYLINDER",
        "color_name": "Blue",
        "dimensions": (0.26, 0.26, 0.031),
        "color": (0.0, 0.0, 1.0, 1.0),
    },
}


def _active_armature(context):
    armature = getattr(context, "active_object", None)
    if not armature or armature.type != "ARMATURE":
        return None
    return armature


def _selected_bone_names(context, armature):
    """Return selected bone names for the active armature in any armature mode."""
    if armature.mode == "POSE":
        return [
            pose_bone.name
            for pose_bone in (context.selected_pose_bones or ())
            if pose_bone.id_data == armature
        ]

    if armature.mode == "EDIT":
        return [bone.name for bone in armature.data.edit_bones if bone.select]

    # Object Mode has no multi-bone selection API; use the armature's active
    # bone as the single bone available to the pairing operator.
    active_bone = armature.data.bones.active
    return [active_bone.name] if active_bone else []


def _capture_context(context, armature):
    active_bone = armature.data.bones.active if armature else None
    return {
        "selected_objects": list(context.selected_objects),
        "active_object": context.view_layer.objects.active,
        "mode": armature.mode if armature else "OBJECT",
        "bone_names": tuple(_selected_bone_names(context, armature))
        if armature
        else (),
        "active_bone_name": active_bone.name if active_bone else "",
    }


def _restore_context(context, state):
    """Restore object selection, active object, armature mode, and bone selection."""
    active_object = state["active_object"]
    mode = state["mode"]

    if context.object and context.object.mode != "OBJECT":
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in state["selected_objects"]:
        if obj and obj.name in bpy.data.objects:
            obj.select_set(True)

    if active_object and active_object.name in bpy.data.objects:
        context.view_layer.objects.active = active_object

    armature = active_object if active_object and active_object.type == "ARMATURE" else None
    if not armature:
        return

    if mode != "OBJECT" and bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode=mode)

    selected_names = set(state["bone_names"])
    if mode == "POSE":
        for pose_bone in armature.pose.bones:
            pose_bone.select = pose_bone.name in selected_names
    elif mode == "EDIT":
        for edit_bone in armature.data.edit_bones:
            edit_bone.select = edit_bone.name in selected_names

    active_bone_name = state["active_bone_name"]
    if active_bone_name and active_bone_name in armature.data.bones:
        armature.data.bones.active = armature.data.bones[active_bone_name]


def _set_world_copy_defaults(constraint):
    constraint.use_x = True
    constraint.use_y = True
    constraint.use_z = True
    constraint.owner_space = "WORLD"
    constraint.target_space = "WORLD"
    constraint.influence = 1.0


def _add_copy_constraints(owner, target, subtarget=None):
    """Add World-space Copy Location and Copy Rotation constraints."""
    for constraint_type, label in (
        ("COPY_LOCATION", "Copy Location"),
        ("COPY_ROTATION", "Copy Rotation"),
    ):
        constraint = owner.constraints.new(type=constraint_type)
        constraint.name = f"Root Motion {label}"
        constraint.target = target
        if subtarget is not None:
            constraint.subtarget = subtarget
        _set_world_copy_defaults(constraint)


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
        raise ValueError(f"Unsupported Root Motion primitive: {primitive}")

    obj = context.object
    if not obj or obj.type != "MESH":
        raise RuntimeError("Blender did not create a mesh Shape Object")
    return obj


def _create_shape_object(context, armature, bone_name, shape_preset):
    target_collection = context.collection or context.scene.collection
    obj = _make_primitive(context, shape_preset["primitive"])

    # Keep the helper in the collection that was active when the operator ran.
    if target_collection:
        for collection in list(obj.users_collection):
            if collection != target_collection:
                collection.objects.unlink(obj)
        if target_collection not in obj.users_collection:
            target_collection.objects.link(obj)

    obj.name = f"{ROOT_MOTION_PREFIX}{bone_name}"
    obj.location = (0.0, 0.0, 0.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.dimensions = shape_preset["dimensions"]
    obj.color = shape_preset["color"]
    _add_copy_constraints(obj, armature, subtarget=bone_name)
    return obj


def _numeric_suffix_key(obj, base_name):
    match = re.fullmatch(rf"{re.escape(base_name)}\.(\d+)", obj.name)
    if match:
        return int(match.group(1))
    return None


def _find_first_matching_object(context, bone_name):
    """Find the exact RM_ name first, then the lowest Blender numeric suffix."""
    base_name = f"{ROOT_MOTION_PREFIX}{bone_name}"
    exact = next(
        (obj for obj in context.scene.objects if obj.name == base_name),
        None,
    )
    if exact:
        return exact

    candidates = []
    for obj in context.scene.objects:
        suffix = _numeric_suffix_key(obj, base_name)
        if suffix is not None:
            candidates.append((suffix, obj.name, obj))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


class ST_OT_CreateRootMotionShape(Operator):
    bl_idname = "script_toolkit.create_root_motion_shape"
    bl_label = "Create Shape Object"
    bl_description = "Create the selected Shape for every selected Pose bone"
    bl_options = {"REGISTER", "UNDO"}

    shape_key: StringProperty(
        name="Shape",
        default="CUBE_BLUE",
        options={"HIDDEN"},
    )

    @classmethod
    def poll(cls, context):
        armature = _active_armature(context)
        return bool(
            armature
            and armature.mode == "POSE"
            and _selected_bone_names(context, armature)
        )

    def execute(self, context):
        armature = _active_armature(context)
        if not armature or armature.mode != "POSE":
            self.report({"ERROR"}, "Select an armature in Pose Mode first.")
            return {"CANCELLED"}

        bone_names = _selected_bone_names(context, armature)
        if not bone_names:
            self.report({"WARNING"}, "Select at least one Pose bone.")
            return {"CANCELLED"}

        shape_preset = SHAPE_PRESETS.get(self.shape_key)
        if shape_preset is None:
            self.report({"ERROR"}, f"Unknown Root Motion Shape: {self.shape_key}")
            return {"CANCELLED"}

        state = _capture_context(context, armature)
        created_objects = []
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
            for bone_name in bone_names:
                created_objects.append(
                    _create_shape_object(context, armature, bone_name, shape_preset)
                )
        except Exception as error:
            for obj in created_objects:
                if obj.name in bpy.data.objects:
                    bpy.data.objects.remove(obj, do_unlink=True)
            _restore_context(context, state)
            self.report({"ERROR"}, f"Could not create Root Motion Shape: {error}")
            return {"CANCELLED"}

        _restore_context(context, state)
        self.report(
            {"INFO"},
            f"Created {len(created_objects)} {shape_preset['shape']} Shape Object(s).",
        )
        return {"FINISHED"}


class ST_OT_AddRootMotionBoneConstraints(Operator):
    bl_idname = "script_toolkit.add_root_motion_bone_constraints"
    bl_label = "Add Bone Constraints"
    bl_description = (
        "Add Copy Location and Copy Rotation to selected bones from the first matching RM_ object"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(_active_armature(context))

    def execute(self, context):
        armature = _active_armature(context)
        if not armature:
            self.report({"ERROR"}, "Select an armature first.")
            return {"CANCELLED"}

        bone_names = _selected_bone_names(context, armature)
        if not bone_names:
            self.report({"WARNING"}, "Select at least one bone.")
            return {"CANCELLED"}

        state = _capture_context(context, armature)
        added = []
        missing = []
        try:
            if armature.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")

            for bone_name in bone_names:
                target_object = _find_first_matching_object(context, bone_name)
                if target_object is None:
                    missing.append(bone_name)
                    continue
                _add_copy_constraints(
                    armature.pose.bones[bone_name],
                    target_object,
                )
                added.append((bone_name, target_object.name))
        except Exception as error:
            _restore_context(context, state)
            self.report({"ERROR"}, f"Could not add Bone Constraints: {error}")
            return {"CANCELLED"}

        _restore_context(context, state)
        if missing:
            self.report(
                {"WARNING"},
                f"Added {len(added)} pair(s); no RM_ object found for: {', '.join(missing)}",
            )
        elif added:
            self.report({"INFO"}, f"Added constraints to {len(added)} bone(s).")

        return {"FINISHED"} if added else {"CANCELLED"}


def draw_ui(layout, context):
    """Draw Root Motion controls inside Script Toolkit's main panel."""
    shape_box = layout.box()
    shape_box.label(text="Create Root Motion Shape", icon="MESH_DATA")
    shape_box.label(
        text="Select one or more bones in Pose Mode, then choose a Shape.",
        icon="INFO",
    )

    for shape_key, shape_preset in SHAPE_PRESETS.items():
        row = shape_box.row(align=True)
        row.label(text=shape_preset["shape"])
        row.label(text=shape_preset["color_name"])
        operator = row.operator(
            ST_OT_CreateRootMotionShape.bl_idname,
            text="Create",
            icon="ADD",
        )
        operator.shape_key = shape_key

    bake_box = layout.box()
    bake_box.label(text="Bake Animation", icon="ACTION")
    bake_box.label(
        text="Open Blender's Object > Animation > Bake Action dialog.",
        icon="INFO",
    )
    bake_box.operator("nla.bake", text="Bake Animation", icon="ACTION")

    constraint_box = layout.box()
    constraint_box.label(text="Bone Constraints", icon="CONSTRAINT_BONE")
    constraint_box.label(
        text="Pair selected bones with the first RM_<Bone> object found.",
        icon="INFO",
    )
    constraint_box.label(
        text="Run Bake Action first and enable Clear Constraints.",
        icon="INFO",
    )
    constraint_box.operator(
        ST_OT_AddRootMotionBoneConstraints.bl_idname,
        text="Add Bone Constraints",
        icon="CONSTRAINT_BONE",
    )


classes = (
    ST_OT_CreateRootMotionShape,
    ST_OT_AddRootMotionBoneConstraints,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
