import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

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


class FakeLayout:
    def __init__(self):
        self.calls = []

    def box(self):
        return self

    def row(self, **_kwargs):
        return self

    def split(self, **_kwargs):
        return self

    def separator(self):
        self.calls.append(("separator",))

    def label(self, **kwargs):
        self.calls.append(("label", kwargs))

    def prop(self, _data, property_name, **kwargs):
        self.calls.append(("prop", property_name, kwargs))

    def operator(self, operator_id, **kwargs):
        operator = SimpleNamespace(target_name="")
        self.calls.append(("operator", operator_id, kwargs, operator))
        return operator

    def template_list(self, *args, **kwargs):
        self.calls.append(("template_list", args, kwargs))


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

        helper_rig = make_armature("IKHelperRig")
        helper_rig.matrix_world = (
            Matrix.Translation((1.0, 2.0, 3.0))
            @ Matrix.Rotation(0.6, 4, "Z")
        )
        bpy.ops.object.select_all(action="DESELECT")
        helper_rig.select_set(True)
        bpy.context.view_layer.objects.active = helper_rig
        bpy.ops.object.mode_set(mode="EDIT")
        source_bone = helper_rig.data.edit_bones.new("DEF-Leg")
        source_bone.head = (0.25, 0.5, 0.75)
        source_bone.tail = (0.25, 0.5, 1.75)
        bpy.ops.object.mode_set(mode="OBJECT")
        helper_rig.data.bones.active = helper_rig.data.bones["DEF-Leg"]

        props.target_armature = target
        assert abs(props.ik_helper_bone_length - 0.05) <= 1e-6
        assert abs(props.ik_pole_distance - 0.21) <= 1e-6
        assert props.ik_helper_preset == "LEG_2"
        assert props.ik_pole_name == "POLE-IK_LEG.L"
        assert props.ik_mch_ik_name == "MCH-IK_LEG.L"
        assert props.ik_foot_name == "FOOT_LEG.L"
        props.ik_helper_bone_length = 0.4
        props.ik_pole_distance = 0.8
        props.ik_pole_name = "POLE-IK_LEG.L"
        props.ik_mch_ik_name = "MCH-IK_LEG.L"
        props.ik_foot_name = "FOOT_LEG.L"

        negative_global_y = (
            helper_rig.matrix_world.to_3x3().inverted_safe()
            @ Vector((0.0, -1.0, 0.0))
        ).normalized()
        positive_global_y = -negative_global_y
        source_head = Vector((0.25, 0.5, 0.75))

        assert bpy.ops.script_toolkit.create_pole_bone() == {"FINISHED"}
        pole = helper_rig.data.bones["POLE-IK_LEG.L"]
        assert_vector_close(
            pole.head_local,
            source_head + negative_global_y * (props.ik_pole_distance + props.ik_helper_bone_length),
        )
        assert_vector_close(
            pole.tail_local,
            source_head + negative_global_y * props.ik_pole_distance,
        )
        assert pole.parent is None
        pole_constraint = next(
            constraint
            for constraint in helper_rig.pose.bones["POLE-IK_LEG.L"].constraints
            if constraint.type == "DAMPED_TRACK"
        )
        assert pole_constraint.target == helper_rig
        assert pole_constraint.subtarget == "DEF-Leg"
        assert pole_constraint.track_axis == "TRACK_Y"
        assert helper_rig.data.bones.active.name == "DEF-Leg"

        assert bpy.ops.script_toolkit.create_mch_ik_bone() == {"FINISHED"}
        mch_ik = helper_rig.data.bones["MCH-IK_LEG.L"]
        assert_vector_close(mch_ik.head_local, source_head)
        assert_vector_close(
            mch_ik.tail_local,
            source_head + positive_global_y * props.ik_helper_bone_length,
        )
        assert mch_ik.parent is None
        assert helper_rig.data.bones.active.name == "DEF-Leg"

        assert bpy.ops.script_toolkit.create_foot_bone() == {"FINISHED"}
        foot = helper_rig.data.bones["FOOT_LEG.L"]
        assert_vector_close(foot.head_local, source_head)
        assert_vector_close(
            foot.tail_local,
            source_head + positive_global_y * props.ik_helper_bone_length,
        )
        assert foot.parent is None
        assert helper_rig.data.bones.active.name == "DEF-Leg"

        assert bpy.ops.script_toolkit.create_foot_bone() == {"CANCELLED"}
        assert len(helper_rig.data.bones) == 4

        props.ik_helper_preset = "LEG_4"
        assert props.ik_pole_front_name == "POLE-IK_LEG_FRONT.L"
        assert props.ik_pole_back_name == "POLE-IK_LEG_BACK.L"
        assert props.ik_mch_ik_front_name == "MCH-IK_LEG_FRONT.L"
        assert props.ik_mch_ik_back_name == "MCH-IK_LEG_BACK.L"
        assert props.ik_foot_front_name == "FOOT_LEG_FRONT.L"
        assert props.ik_foot_back_name == "FOOT_LEG_BACK.L"

        assert bpy.ops.script_toolkit.create_pole_bone(
            target_name=props.ik_pole_front_name
        ) == {"FINISHED"}
        assert bpy.ops.script_toolkit.create_pole_bone(
            target_name=props.ik_pole_back_name
        ) == {"FINISHED"}
        assert bpy.ops.script_toolkit.create_mch_ik_bone(
            target_name=props.ik_mch_ik_front_name
        ) == {"FINISHED"}
        assert bpy.ops.script_toolkit.create_mch_ik_bone(
            target_name=props.ik_mch_ik_back_name
        ) == {"FINISHED"}
        assert bpy.ops.script_toolkit.create_foot_bone(
            target_name=props.ik_foot_front_name
        ) == {"FINISHED"}
        assert bpy.ops.script_toolkit.create_foot_bone(
            target_name=props.ik_foot_back_name
        ) == {"FINISHED"}
        assert {
            bone.name
            for bone in helper_rig.data.bones
            if bone.name.endswith("_FRONT.L") or bone.name.endswith("_BACK.L")
        } == {
            "POLE-IK_LEG_FRONT.L",
            "POLE-IK_LEG_BACK.L",
            "MCH-IK_LEG_FRONT.L",
            "MCH-IK_LEG_BACK.L",
            "FOOT_LEG_FRONT.L",
            "FOOT_LEG_BACK.L",
        }
        assert all(
            helper_rig.data.bones[name].parent is None
            for name in (
                "POLE-IK_LEG_FRONT.L",
                "POLE-IK_LEG_BACK.L",
                "MCH-IK_LEG_FRONT.L",
                "MCH-IK_LEG_BACK.L",
                "FOOT_LEG_FRONT.L",
                "FOOT_LEG_BACK.L",
            )
        )

        fake_layout = FakeLayout()
        addon.empty_to_bone.draw_ui(
            fake_layout,
            SimpleNamespace(active_object=helper_rig, scene=bpy.context.scene),
        )
        assert any(
            call[0] == "prop" and call[1] == "ik_helper_preset"
            for call in fake_layout.calls
        )
        helper_operator_calls = [
            call
            for call in fake_layout.calls
            if call[0] == "operator"
            and call[1]
            in {
                "script_toolkit.create_pole_bone",
                "script_toolkit.create_mch_ik_bone",
                "script_toolkit.create_foot_bone",
            }
        ]
        assert [call[3].target_name for call in helper_operator_calls[-6:]] == [
            "POLE-IK_LEG_FRONT.L",
            "POLE-IK_LEG_BACK.L",
            "MCH-IK_LEG_FRONT.L",
            "MCH-IK_LEG_BACK.L",
            "FOOT_LEG_FRONT.L",
            "FOOT_LEG_BACK.L",
        ]

        constraint_operator_ids = [
            call[1]
            for call in fake_layout.calls
            if call[0] == "operator"
            and call[1]
            in {
                "script_toolkit.create_ik_target",
                "script_toolkit.set_pole_target",
            }
        ]
        assert constraint_operator_ids == [
            "script_toolkit.create_ik_target",
            "script_toolkit.set_pole_target",
        ]
        assert any(
            call[0] == "label"
            and call[1]["text"] == "IK Target: select Target, then IK Bone (active last)."
            for call in fake_layout.calls
        )
        assert any(
            call[0] == "label"
            and call[1]["text"] == "Pole Target: select Pole, then IK Bone (active last)."
            for call in fake_layout.calls
        )

        bpy.ops.object.mode_set(mode="POSE")
        for pose_bone in helper_rig.pose.bones:
            pose_bone.select = False
        helper_rig.pose.bones["FOOT_LEG_FRONT.L"].select = True
        helper_rig.pose.bones["DEF-Leg"].select = True
        helper_rig.data.bones.active = helper_rig.data.bones["DEF-Leg"]

        assert bpy.ops.script_toolkit.create_ik_target() == {"FINISHED"}
        ik_pose_bone = helper_rig.pose.bones["DEF-Leg"]
        ik_constraints = [
            constraint for constraint in ik_pose_bone.constraints if constraint.type == "IK"
        ]
        assert len(ik_constraints) == 1
        assert ik_constraints[0].name == "IK Target"
        assert ik_constraints[0].target == helper_rig
        assert ik_constraints[0].subtarget == "FOOT_LEG_FRONT.L"
        assert ik_constraints[0].chain_count == 2
        assert abs(ik_constraints[0].pole_angle) <= 1e-6

        assert bpy.ops.script_toolkit.create_ik_target() == {"CANCELLED"}
        assert bpy.ops.script_toolkit.create_ik_target(
            confirm_duplicate=True
        ) == {"FINISHED"}
        ik_constraints = [
            constraint for constraint in ik_pose_bone.constraints if constraint.type == "IK"
        ]
        assert len(ik_constraints) == 2

        for pose_bone in helper_rig.pose.bones:
            pose_bone.select = False
        helper_rig.pose.bones["POLE-IK_LEG_FRONT.L"].select = True
        helper_rig.pose.bones["DEF-Leg"].select = True
        helper_rig.data.bones.active = helper_rig.data.bones["DEF-Leg"]
        assert bpy.ops.script_toolkit.set_pole_target() == {"FINISHED"}
        pole_constraint = next(
            constraint
            for constraint in helper_rig.pose.bones["POLE-IK_LEG_FRONT.L"].constraints
            if constraint.type == "DAMPED_TRACK"
        )
        assert pole_constraint.mute is False
        for constraint in ik_constraints:
            assert constraint.pole_target == helper_rig
            assert constraint.pole_subtarget == "POLE-IK_LEG_FRONT.L"

        for pose_bone in helper_rig.pose.bones:
            pose_bone.select = False
        helper_rig.pose.bones["MCH-IK_LEG_FRONT.L"].select = True
        helper_rig.pose.bones["DEF-Leg"].select = True
        helper_rig.data.bones.active = helper_rig.data.bones["DEF-Leg"]
        assert bpy.ops.script_toolkit.set_pole_target() == {"FINISHED"}
        for constraint in ik_constraints:
            assert constraint.pole_target == helper_rig
            assert constraint.pole_subtarget == "MCH-IK_LEG_FRONT.L"

        for pose_bone in helper_rig.pose.bones:
            pose_bone.select = False
        helper_rig.pose.bones["POLE-IK_LEG_BACK.L"].select = True
        helper_rig.pose.bones["FOOT_LEG_BACK.L"].select = True
        helper_rig.data.bones.active = helper_rig.data.bones["FOOT_LEG_BACK.L"]
        assert bpy.ops.script_toolkit.set_pole_target() == {"CANCELLED"}
        bpy.ops.object.mode_set(mode="OBJECT")

        foreign_rig = make_armature("ForeignRig")
        bpy.ops.object.select_all(action="DESELECT")
        foreign_rig.select_set(True)
        bpy.context.view_layer.objects.active = foreign_rig
        bpy.ops.object.mode_set(mode="EDIT")
        foreign_bone = foreign_rig.data.edit_bones.new("Foreign")
        foreign_bone.head = (0.0, 0.0, 0.0)
        foreign_bone.tail = (0.0, 0.0, 1.0)
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        helper_rig.select_set(True)
        foreign_rig.select_set(True)
        bpy.context.view_layer.objects.active = helper_rig
        bpy.ops.object.mode_set(mode="POSE")
        for pose_bone in helper_rig.pose.bones:
            pose_bone.select = False
        for pose_bone in foreign_rig.pose.bones:
            pose_bone.select = False
        helper_rig.pose.bones["FOOT_LEG_FRONT.L"].select = True
        helper_rig.pose.bones["DEF-Leg"].select = True
        foreign_rig.pose.bones["Foreign"].select = True
        helper_rig.data.bones.active = helper_rig.data.bones["DEF-Leg"]
        assert bpy.ops.script_toolkit.create_ik_target() == {"CANCELLED"}
        bpy.ops.object.mode_set(mode="OBJECT")
        print("IK_HELPER_BONES_OK")
    finally:
        addon.unregister()


if __name__ == "__main__":
    run()
