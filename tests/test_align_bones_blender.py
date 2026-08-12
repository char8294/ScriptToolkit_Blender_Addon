"""Blender background tests for Align Bones axis conversion."""

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


def assert_matrix_close(actual, expected, tolerance=1e-5):
    for row in range(4):
        for column in range(4):
            assert abs(actual[row][column] - expected[row][column]) <= tolerance


def assert_vector_close(actual, expected, tolerance=1e-5):
    assert (Vector(actual) - Vector(expected)).length <= tolerance


def make_armature(name):
    armature_data = bpy.data.armatures.new(f"{name}_Data")
    armature = bpy.data.objects.new(name, armature_data)
    bpy.context.scene.collection.objects.link(armature)
    return armature


def make_weighted_mesh(name, armature):
    mesh_data = bpy.data.meshes.new(f"{name}_Data")
    mesh_data.from_pydata(
        [(0.0, 1.0, 0.0), (3.0, 1.0, 0.0)],
        [],
        [],
    )
    mesh = bpy.data.objects.new(name, mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    selected_group = mesh.vertex_groups.new(name="Selected")
    selected_group.add([0], 1.0, "REPLACE")
    untouched_group = mesh.vertex_groups.new(name="Untouched")
    untouched_group.add([1], 1.0, "REPLACE")
    modifier = mesh.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = armature
    return mesh


def evaluated_vertex_positions(mesh):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_mesh = mesh.evaluated_get(depsgraph)
    return [vertex.co.copy() for vertex in evaluated_mesh.data.vertices]


def run():
    addon = load_addon()
    addon.register()
    armature = make_armature("AxisConversionRig")
    mesh = None

    try:
        bpy.context.view_layer.objects.active = armature
        armature.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")

        selected = armature.data.edit_bones.new("Selected")
        selected.head = (0.0, 0.0, 0.0)
        selected.tail = (0.0, 1.0, 0.0)

        untouched = armature.data.edit_bones.new("Untouched")
        untouched.head = (3.0, 0.0, 0.0)
        untouched.tail = (3.0, 1.0, 0.0)
        connected = armature.data.edit_bones.new("Connected")
        connected.head = selected.tail
        connected.tail = (0.0, 2.0, 0.0)
        connected.parent = selected
        connected.use_connect = True
        selected.select = True
        untouched.select = False
        connected.select = False

        mesh = make_weighted_mesh("AxisConversionMesh", armature)

        bpy.ops.object.mode_set(mode="POSE")
        selected_pose = armature.pose.bones["Selected"]
        untouched_pose = armature.pose.bones["Untouched"]
        selected_pose.rotation_mode = "XYZ"
        selected_pose.rotation_euler = (0.2, -0.35, 0.55)
        untouched_pose.rotation_mode = "XYZ"
        untouched_pose.rotation_euler = (-0.1, 0.25, -0.4)
        armature.animation_data_create()
        bpy.context.scene.frame_set(1)
        selected_pose.keyframe_insert(data_path="rotation_euler", frame=1)
        untouched_pose.keyframe_insert(data_path="rotation_euler", frame=1)
        bpy.context.view_layer.update()
        mesh_before = evaluated_vertex_positions(mesh)
        pose_before = {
            pose_bone.name: pose_bone.matrix.copy()
            for pose_bone in armature.pose.bones
        }

        bpy.ops.object.mode_set(mode="EDIT")
        edit_bones = armature.data.edit_bones
        selected_matrix_before = edit_bones["Selected"].matrix.copy()
        untouched_matrix_before = edit_bones["Untouched"].matrix.copy()
        connected_matrix_before = edit_bones["Connected"].matrix.copy()

        props = bpy.context.scene.script_toolkit
        assert props.bone_axis_source_primary == "Y"
        assert props.bone_axis_source_secondary == "X"
        assert props.bone_axis_target_primary == "X"
        assert props.bone_axis_target_secondary == "Y"

        correction = (
            addon.align_bones.bone_axis_matrix("Y", "X").inverted_safe()
            @ addon.align_bones.bone_axis_matrix("X", "Y")
        )

        assert bpy.ops.script_toolkit.convert_bone_axes() == {"FINISHED"}
        assert bpy.context.mode == "EDIT_ARMATURE"
        assert_matrix_close(
            edit_bones["Selected"].matrix,
            selected_matrix_before @ correction,
        )
        assert_matrix_close(edit_bones["Untouched"].matrix, untouched_matrix_before)
        assert_matrix_close(edit_bones["Connected"].matrix, connected_matrix_before)

        bpy.ops.object.mode_set(mode="POSE")
        bpy.context.view_layer.update()
        mesh_after = evaluated_vertex_positions(mesh)
        for actual, expected in zip(mesh_after, mesh_before):
            assert_vector_close(actual, expected)
        assert_matrix_close(
            armature.pose.bones["Untouched"].matrix,
            pose_before["Untouched"],
        )

        # The current evaluated pose must also survive re-evaluation of an
        # existing action after leaving Edit Mode.
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.mode_set(mode="POSE")
        bpy.context.view_layer.update()
        mesh_after = evaluated_vertex_positions(mesh)
        for actual, expected in zip(mesh_after, mesh_before):
            assert_vector_close(actual, expected)
        bpy.ops.object.mode_set(mode="EDIT")

        # Invalid source pairs are rejected without modifying the selected bone.
        invalid_before = edit_bones["Selected"].matrix.copy()
        props.bone_axis_source_primary = "X"
        props.bone_axis_source_secondary = "-X"
        try:
            result = bpy.ops.script_toolkit.convert_bone_axes()
        except RuntimeError as error:
            assert "different axes" in str(error)
            result = {"CANCELLED"}
        assert result == {"CANCELLED"}
        assert_matrix_close(edit_bones["Selected"].matrix, invalid_before)

        # Restore valid settings for the next registration/cleanup assertions.
        props.bone_axis_source_primary = "Y"
        props.bone_axis_source_secondary = "X"
        print("ALIGN_BONES_AXIS_CONVERSION_OK")
    finally:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if mesh is not None:
            mesh_data = mesh.data
            bpy.data.objects.remove(mesh, do_unlink=True)
            bpy.data.meshes.remove(mesh_data)
        bpy.data.objects.remove(armature, do_unlink=True)
        addon.unregister()


if __name__ == "__main__":
    run()
