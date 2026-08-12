"""Blender background tests for the Create Root Motion tool."""

import importlib.util
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def load_addon():
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "script_toolkit",
        repo_root / "__init__.py",
        submodule_search_locations=[str(repo_root)],
    )
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    spec.loader.exec_module(addon)
    return addon


def make_armature(name):
    armature_data = bpy.data.armatures.new(f"{name}_Data")
    armature = bpy.data.objects.new(name, armature_data)
    bpy.context.scene.collection.objects.link(armature)
    return armature


def assert_vector_close(actual, expected, tolerance=1e-5):
    assert (Vector(actual) - Vector(expected)).length <= tolerance


def add_bones(armature, names):
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for index, name in enumerate(names):
        bone = armature.data.edit_bones.new(name)
        bone.head = (float(index) * 2.0, 0.0, 0.0)
        bone.tail = (float(index) * 2.0, 0.0, 1.0)
    bpy.ops.object.mode_set(mode="OBJECT")


def select_pose_bone(armature, bone_name):
    if armature.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    if armature.mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE")
    for pose_bone in armature.pose.bones:
        pose_bone.select = pose_bone.name == bone_name
    armature.data.bones.active = armature.data.bones[bone_name]


def root_motion_objects(addon, armature, bone_name):
    return [
        obj
        for obj in bpy.data.objects
        if obj.get(addon.root_motion.ROOT_MOTION_MARKER, False)
        and obj.get(addon.root_motion.ROOT_MOTION_ARMATURE) == armature.name
        and obj.get(addon.root_motion.ROOT_MOTION_BONE) == bone_name
    ]


def run():
    addon = load_addon()
    addon.register()
    armature = make_armature("RootMotionTestRig")
    created_objects = []

    try:
        names = tuple(addon.root_motion.SHAPE_SPECS)
        add_bones(armature, names)
        props = bpy.context.scene.script_toolkit
        assert props.tool == "REEXPORT"
        props.tool = "ROOT_MOTION"
        assert (
            props.bl_rna.properties["tool"].enum_items["ROOT_MOTION"].name
            == "Create Root Motion"
        )

        for bone_name, spec in addon.root_motion.SHAPE_SPECS.items():
            select_pose_bone(armature, bone_name)
            assert bpy.ops.script_toolkit.create_root_motion_shape() == {"FINISHED"}
            obj = next(
                candidate
                for candidate in bpy.data.objects
                if candidate.get(addon.root_motion.ROOT_MOTION_BONE) == bone_name
                and candidate.get(addon.root_motion.ROOT_MOTION_ARMATURE)
                == armature.name
                and candidate not in created_objects
            )
            created_objects.append(obj)

            assert obj.name.startswith(f"RM_{bone_name}")
            assert_vector_close(obj.location, (0.0, 0.0, 0.0))
            for actual, expected in zip(obj.dimensions, spec["dimensions"]):
                assert abs(actual - expected) <= 1e-5
            assert obj.users_collection
            current_collection = bpy.context.collection
            if current_collection:
                assert current_collection in obj.users_collection
            assert {constraint.type for constraint in obj.constraints} == {
                "COPY_LOCATION",
                "COPY_ROTATION",
            }
            assert all(
                constraint.target == armature
                and constraint.subtarget == bone_name
                for constraint in obj.constraints
            )

            if "color" in spec:
                assert_vector_close(obj.color[:3], spec["color"][:3])

        # Creating the same shape again is intentionally allowed and gets a
        # Blender-generated suffix instead of replacing the first object.
        select_pose_bone(armature, "Root")
        assert bpy.ops.script_toolkit.create_root_motion_shape() == {"FINISHED"}
        duplicate = next(
            obj
            for obj in root_motion_objects(addon, armature, "Root")
            if obj not in created_objects
        )
        created_objects.append(duplicate)
        assert duplicate.name != created_objects[0].name

        # Keep the original Bone Action so the inverse constraint workflow can
        # be verified without changing the source animation.
        scene = bpy.context.scene
        scene.frame_start = 1
        scene.frame_end = 3
        root_pose_bone = armature.pose.bones["Root"]
        armature.animation_data_create()
        scene.frame_set(1)
        root_pose_bone.location = (0.0, 0.0, 0.0)
        root_pose_bone.keyframe_insert(data_path="location", frame=1)
        scene.frame_set(3)
        root_pose_bone.location = (1.0, 0.0, 0.0)
        root_pose_bone.keyframe_insert(data_path="location", frame=3)
        original_action = armature.animation_data.action
        original_frame_range = tuple(original_action.frame_range)

        # Duplicate helper objects are supported, but Bake must be explicit
        # about which one is selected.
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        created_objects[0].select_set(True)
        duplicate.select_set(True)
        bpy.context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode="POSE")
        for pose_bone in armature.pose.bones:
            pose_bone.select = pose_bone.name == "Root"
        armature.data.bones.active = armature.data.bones["Root"]
        assert bpy.ops.script_toolkit.bake_root_motion(
            frame_start=1,
            frame_end=3,
            frame_step=1,
        ) == {"CANCELLED"}

        # Bake only the selected Root shape. The duplicate remains constrained
        # to the bone and is not included in the bake.
        select_pose_bone(armature, "Root")
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        created_objects[0].select_set(True)
        bpy.context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode="POSE")
        for pose_bone in armature.pose.bones:
            pose_bone.select = pose_bone.name == "Root"
        armature.data.bones.active = armature.data.bones["Root"]
        assert bpy.ops.script_toolkit.bake_root_motion(
            frame_start=1,
            frame_end=3,
            frame_step=1,
        ) == {"FINISHED"}

        baked_object = created_objects[0]
        assert baked_object.animation_data
        assert baked_object.animation_data.action
        assert not any(
            constraint.type in {"COPY_LOCATION", "COPY_ROTATION"}
            for constraint in baked_object.constraints
        )
        assert armature.animation_data.action == original_action
        assert tuple(original_action.frame_range) == original_frame_range

        bone_constraints = [
            constraint
            for constraint in root_pose_bone.constraints
            if constraint.target == baked_object
        ]
        assert {constraint.type for constraint in bone_constraints} == {
            "COPY_LOCATION",
            "COPY_ROTATION",
        }
        assert len(root_motion_objects(addon, armature, "Root")) == 2
        assert any(
            constraint.type == "COPY_LOCATION"
            for constraint in duplicate.constraints
        )

        scene.frame_set(3)
        bpy.context.view_layer.update()
        bone_world = armature.matrix_world @ root_pose_bone.matrix
        assert_vector_close(
            baked_object.matrix_world.translation,
            bone_world.translation,
            tolerance=1e-4,
        )
        print("ROOT_MOTION_CREATE_AND_BAKE_OK")
    finally:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(created_objects):
            if obj.name in bpy.data.objects:
                mesh_data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if mesh_data.users == 0:
                    bpy.data.meshes.remove(mesh_data)
        if armature.name in bpy.data.objects:
            armature_data = armature.data
            bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data.users == 0:
                bpy.data.armatures.remove(armature_data)
        addon.unregister()


if __name__ == "__main__":
    run()
