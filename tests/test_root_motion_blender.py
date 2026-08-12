"""Blender background tests for the Create Root Motion tool."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

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


class FakeLayout:
    def __init__(self):
        self.calls = []

    def box(self):
        return self

    def row(self, **_kwargs):
        return self

    def separator(self):
        self.calls.append(("separator",))

    def label(self, **kwargs):
        self.calls.append(("label", kwargs))

    def operator(self, operator_id, **kwargs):
        operator = SimpleNamespace(shape_key="")
        self.calls.append(("operator", operator_id, kwargs, operator))
        return operator


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


def select_bones(armature, names, mode="POSE"):
    if armature.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature

    if mode != "OBJECT":
        bpy.ops.object.mode_set(mode=mode)

    selected_names = set(names)
    if mode == "POSE":
        for pose_bone in armature.pose.bones:
            pose_bone.select = pose_bone.name in selected_names
    elif mode == "EDIT":
        for edit_bone in armature.data.edit_bones:
            edit_bone.select = edit_bone.name in selected_names

    if names:
        armature.data.bones.active = armature.data.bones[names[0]]


def generated_object(armature, bone_name, suffix=""):
    return bpy.data.objects.get(f"RM_{bone_name}{suffix}")


def assert_copy_constraints(constraints, target, subtarget=None):
    assert {constraint.type for constraint in constraints} == {
        "COPY_LOCATION",
        "COPY_ROTATION",
    }
    assert all(constraint.target == target for constraint in constraints)
    if subtarget is not None:
        assert all(constraint.subtarget == subtarget for constraint in constraints)
    assert all(
        constraint.influence == 1.0
        and constraint.use_x
        and constraint.use_y
        and constraint.use_z
        for constraint in constraints
    )


def run():
    addon = load_addon()
    addon.register()
    armature = make_armature("RootMotionTestRig")
    created_objects = []

    try:
        add_bones(
            armature,
            ("Root", "Hand.L", "EditBone", "ObjectBone", "SuffixBone", "Missing"),
        )
        props = bpy.context.scene.script_toolkit
        assert props.tool == "REEXPORT"
        props.tool = "ROOT_MOTION"
        assert props.bl_rna.properties["tool"].enum_items["ROOT_MOTION"].name == (
            "Create Root Motion"
        )

        fake_layout = FakeLayout()
        addon.root_motion.draw_ui(
            fake_layout,
            SimpleNamespace(scene=bpy.context.scene, active_object=armature),
        )
        shape_operator_calls = [
            call
            for call in fake_layout.calls
            if call[0] == "operator"
            and call[1] == "script_toolkit.create_root_motion_shape"
        ]
        assert [call[3].shape_key for call in shape_operator_calls] == [
            "CUBE_BLUE",
            "ICO_SPHERE_YELLOW",
            "CYLINDER_RED",
            "CYLINDER_BLUE",
        ]
        assert any(
            call[0] == "operator" and call[1] == "nla.bake"
            for call in fake_layout.calls
        )
        assert any(
            call[0] == "operator"
            and call[1] == "script_toolkit.add_root_motion_bone_constraints"
            for call in fake_layout.calls
        )
        assert not hasattr(addon.root_motion, "ST_OT_BakeRootMotion")

        # One Create button applies its selected Shape to every selected bone.
        select_bones(armature, ("Root", "Hand.L"), mode="POSE")
        assert bpy.ops.script_toolkit.create_root_motion_shape(
            shape_key="CUBE_BLUE"
        ) == {"FINISHED"}
        assert armature.mode == "POSE"
        assert addon.root_motion._selected_bone_names(
            bpy.context, armature
        ) == ["Root", "Hand.L"]

        root_cube = generated_object(armature, "Root")
        hand_cube = generated_object(armature, "Hand.L")
        created_objects.extend((root_cube, hand_cube))
        for obj, bone_name in ((root_cube, "Root"), (hand_cube, "Hand.L")):
            assert obj is not None
            assert_vector_close(obj.location, (0.0, 0.0, 0.0))
            assert_vector_close(obj.dimensions, (0.16, 0.16, 0.16))
            assert_vector_close(obj.color[:3], (0.0, 0.5, 1.0))
            assert obj.users_collection
            assert_copy_constraints(obj.constraints, armature, bone_name)

        # The remaining presets can be applied to arbitrary bone names.
        preset_bones = (
            ("ICO_SPHERE_YELLOW", "ObjectBone", (0.143, 0.15, 0.15), (1.0, 1.0, 0.0)),
            ("CYLINDER_RED", "EditBone", (0.26, 0.26, 0.031), (1.0, 0.0, 0.0)),
            ("CYLINDER_BLUE", "SuffixBone", (0.26, 0.26, 0.031), (0.0, 0.0, 1.0)),
        )
        for shape_key, bone_name, dimensions, color in preset_bones:
            select_bones(armature, (bone_name,), mode="POSE")
            assert bpy.ops.script_toolkit.create_root_motion_shape(
                shape_key=shape_key
            ) == {"FINISHED"}
            obj = generated_object(armature, bone_name)
            created_objects.append(obj)
            assert obj is not None
            assert_vector_close(obj.dimensions, dimensions)
            assert_vector_close(obj.color[:3], color)
            assert_copy_constraints(obj.constraints, armature, bone_name)
            assert armature.mode == "POSE"

        # Duplicates use Blender's numeric suffixes and are not replacements.
        select_bones(armature, ("Root",), mode="POSE")
        assert bpy.ops.script_toolkit.create_root_motion_shape(
            shape_key="CYLINDER_BLUE"
        ) == {"FINISHED"}
        root_duplicate = generated_object(armature, "Root", ".001")
        created_objects.append(root_duplicate)
        assert root_duplicate is not None
        assert root_duplicate.name == "RM_Root.001"

        # Bone constraints pair by exact RM_<Bone> name first.
        select_bones(armature, ("Root", "Hand.L"), mode="POSE")
        assert bpy.ops.script_toolkit.add_root_motion_bone_constraints() == {
            "FINISHED"
        }
        assert armature.mode == "POSE"
        assert_copy_constraints(
            armature.pose.bones["Root"].constraints,
            root_cube,
        )
        assert_copy_constraints(
            armature.pose.bones["Hand.L"].constraints,
            hand_cube,
        )

        # If the base name is absent, the first numeric suffix is accepted.
        suffix_obj = generated_object(armature, "SuffixBone")
        suffix_obj.name = "RM_SuffixBone.001"
        select_bones(armature, ("SuffixBone",), mode="POSE")
        assert bpy.ops.script_toolkit.add_root_motion_bone_constraints() == {
            "FINISHED"
        }
        assert_copy_constraints(
            armature.pose.bones["SuffixBone"].constraints,
            suffix_obj,
        )

        # The pairing operator works in Object, Edit, and Pose modes while
        # preserving whichever mode was active before the click.
        for mode, bone_name in (("OBJECT", "ObjectBone"), ("EDIT", "EditBone")):
            select_bones(armature, (bone_name,), mode=mode)
            assert bpy.ops.script_toolkit.add_root_motion_bone_constraints() == {
                "FINISHED"
            }
            assert armature.mode == mode
            pose_constraints = armature.pose.bones[bone_name].constraints
            assert any(
                constraint.target == generated_object(armature, bone_name)
                for constraint in pose_constraints
            )

        # Missing pairings are skipped while valid selected bones still work.
        select_bones(armature, ("Missing", "Root"), mode="POSE")
        assert bpy.ops.script_toolkit.add_root_motion_bone_constraints() == {
            "FINISHED"
        }
        assert not armature.pose.bones["Missing"].constraints
        print("ROOT_MOTION_PRESETS_AND_CONSTRAINT_PAIRING_OK")
    finally:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in list(created_objects):
            if obj and obj.name in bpy.data.objects:
                mesh_data = obj.data if obj.type == "MESH" else None
                bpy.data.objects.remove(obj, do_unlink=True)
                if mesh_data and mesh_data.users == 0:
                    bpy.data.meshes.remove(mesh_data)
        if armature.name in bpy.data.objects:
            armature_data = armature.data
            bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data.users == 0:
                bpy.data.armatures.remove(armature_data)
        addon.unregister()


if __name__ == "__main__":
    run()
