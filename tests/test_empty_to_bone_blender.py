import importlib.util
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector


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


def run():
    addon = load_addon()
    addon.register()
    try:
        target = make_armature("TargetRig")
        target.matrix_world = (
            Matrix.Translation((2.0, -1.0, 0.5))
            @ Matrix.Rotation(0.35, 4, "Z")
        )

        source = make_armature("SourceRig")
        source.matrix_world = (
            Matrix.Translation((5.0, 3.0, 2.0))
            @ Matrix.Rotation(0.8, 4, "X")
            @ Matrix.Rotation(-0.25, 4, "Z")
        )

        bpy.ops.object.select_all(action="DESELECT")
        source.select_set(True)
        bpy.context.view_layer.objects.active = source

        props = bpy.context.scene.script_toolkit
        props.target_armature = target
        props.bone_length = 0.75

        source_in_target = target.matrix_world.inverted() @ source.matrix_world
        expected_head = source_in_target.translation
        expected_y_axis = (
            source_in_target.to_3x3() @ Vector((0.0, 1.0, 0.0))
        ).normalized()
        expected_tail = expected_head + expected_y_axis * props.bone_length

        assert bpy.ops.script_toolkit.empty_to_bone() == {"FINISHED"}

        created_bone = target.data.bones[source.name]
        assert_vector_close(created_bone.head_local, expected_head)
        assert_vector_close(created_bone.tail_local, expected_tail)
        assert len(target.data.bones) == 1
        assert list(bpy.context.selected_objects) == [source]
        assert bpy.context.view_layer.objects.active == source

        # The target armature itself can also be the selected source. Its
        # origin maps to (0, 0, 0) in its own armature space.
        self_target = make_armature("SelfTargetRig")
        self_target.matrix_world = (
            Matrix.Translation((-3.0, 4.0, 1.0))
            @ Matrix.Rotation(-0.6, 4, "Z")
        )
        bpy.ops.object.select_all(action="DESELECT")
        self_target.select_set(True)
        bpy.context.view_layer.objects.active = self_target
        props.target_armature = self_target
        props.bone_length = 0.5

        assert bpy.ops.script_toolkit.empty_to_bone() == {"FINISHED"}

        self_bone = self_target.data.bones[self_target.name]
        assert_vector_close(self_bone.head_local, (0.0, 0.0, 0.0))
        assert_vector_close(self_bone.tail_local, (0.0, 0.5, 0.0))
        assert len(self_target.data.bones) == 1
        print("EMPTY_TO_BONE_ARMATURE_ORIGIN_OK")
    finally:
        addon.unregister()


if __name__ == "__main__":
    run()
