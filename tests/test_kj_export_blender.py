import importlib.util
import sys
import tempfile
from pathlib import Path

import bmesh
import bpy


EXPORT_CAPTURES = []
SOURCE_OBJECT_STATES = []


class TEST_OT_better_export_fbx(bpy.types.Operator):
    bl_idname = "better_export.fbx"
    bl_label = "Test Better FBX Export"

    filepath: bpy.props.StringProperty()
    use_selection: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        selected = list(context.selected_objects)
        armature = next(obj for obj in selected if obj.type == "ARMATURE")
        mesh = next(obj for obj in selected if obj.type == "MESH")
        depsgraph = context.evaluated_depsgraph_get()
        evaluated_mesh = bpy.data.meshes.new_from_object(
            mesh.evaluated_get(depsgraph),
            preserve_all_data_layers=True,
            depsgraph=depsgraph,
        )
        modifier_targets = {
            modifier.object
            for modifier in mesh.modifiers
            if modifier.type == "ARMATURE"
        }
        EXPORT_CAPTURES.append(
            {
                "mesh_name": mesh.name,
                "mesh_data_name": mesh.data.name,
                "mesh_mode": mesh.mode,
                "first_vertex_x": mesh.data.vertices[0].co.x,
                "polygon_material_order": tuple(
                    polygon.material_index for polygon in mesh.data.polygons
                ),
                "evaluated_polygon_material_order": tuple(
                    polygon.material_index for polygon in evaluated_mesh.polygons
                ),
                "bones": set(armature.data.bones.keys()),
                "modifier_uses_export_armature": armature in modifier_targets,
                "source_data_unchanged": all(
                    source.data == expected_data
                    for source, _expected_name, expected_data in SOURCE_OBJECT_STATES
                ),
            }
        )
        bpy.data.meshes.remove(evaluated_mesh)
        return {"FINISHED"}


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


def make_weighted_rig():
    armature_data = bpy.data.armatures.new("KJ_Test_Armature_Data")
    armature = bpy.data.objects.new("KJ_Test_Armature", armature_data)
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    root = armature_data.edit_bones.new("Root")
    root.head = (0.0, 0.0, 0.0)
    root.tail = (0.0, 0.0, 1.0)
    spine = armature_data.edit_bones.new("Spine")
    spine.head = root.tail
    spine.tail = (0.0, 0.0, 2.0)
    spine.parent = root
    hand = armature_data.edit_bones.new("Hand")
    hand.head = spine.tail
    hand.tail = (0.0, 0.0, 3.0)
    hand.parent = spine
    unused = armature_data.edit_bones.new("Unused")
    unused.head = root.tail
    unused.tail = (1.0, 0.0, 2.0)
    unused.parent = root
    bpy.ops.object.mode_set(mode="OBJECT")
    armature.select_set(False)

    mesh = make_weighted_mesh("KJ_Test_Mesh", armature, "Hand")
    zero_group = mesh.vertex_groups.new(name="Unused")
    zero_group.add([0], 0.0, "REPLACE")
    return armature, mesh


def make_weighted_mesh(name, armature, weighted_bone_name):
    mesh_data = bpy.data.meshes.new(f"{name}_Data")
    mesh_data.from_pydata([(0.0, 0.0, 0.0)], [], [])
    mesh = bpy.data.objects.new(name, mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    weighted_group = mesh.vertex_groups.new(name=weighted_bone_name)
    weighted_group.add([0], 1.0, "REPLACE")
    modifier = mesh.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = armature
    return mesh


def make_material_sort_mesh(name, armature):
    mesh_data = bpy.data.meshes.new(f"{name}_Data")
    vertices = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (2.0, 0.0, 0.0),
        (3.0, 0.0, 0.0),
        (2.0, 1.0, 0.0),
        (4.0, 0.0, 0.0),
        (5.0, 0.0, 0.0),
        (4.0, 1.0, 0.0),
    ]
    mesh_data.from_pydata(vertices, [], [(0, 1, 2), (3, 4, 5), (6, 7, 8)])
    mesh = bpy.data.objects.new(name, mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.data.materials.append(bpy.data.materials.new(f"{name}_Material_0"))
    mesh.data.materials.append(bpy.data.materials.new(f"{name}_Material_1"))
    for polygon, material_index in zip(mesh.data.polygons, (1, 0, 1)):
        polygon.material_index = material_index
    weighted_group = mesh.vertex_groups.new(name="Hand")
    weighted_group.add(range(len(vertices)), 1.0, "REPLACE")
    modifier = mesh.modifiers.new(name="Armature", type="ARMATURE")
    modifier.object = armature
    return mesh


def run():
    addon = load_addon()
    bpy.utils.register_class(TEST_OT_better_export_fbx)
    addon.register()
    try:
        scene = bpy.context.scene
        assert hasattr(scene, "batch_better_fbx_props")
        assert hasattr(bpy.ops.export, "batch_better_fbx_add_mesh")
        assert hasattr(bpy.ops.export, "batch_better_fbx")
        assert addon.ST_Properties.bl_rna.properties["tool"].enum_items.get("KJ_EXPORT")
        assert not scene.batch_better_fbx_props.remove_unused_bones

        armature, mesh = make_weighted_rig()
        keep_names = addon.kj_export._weighted_bone_names_with_ancestors(mesh, armature)
        assert keep_names == {"Root", "Spine", "Hand"}

        temp_armature = addon.kj_export._duplicate_armature(bpy.context, armature)
        addon.kj_export._remove_bones_except(bpy.context, temp_armature, keep_names)
        assert set(temp_armature.data.bones.keys()) == keep_names
        assert set(armature.data.bones.keys()) == {"Root", "Spine", "Hand", "Unused"}
        temp_armature_data = temp_armature.data
        bpy.data.objects.remove(temp_armature, do_unlink=True)
        bpy.data.armatures.remove(temp_armature_data)

        temp_mesh = addon.kj_export._duplicate_mesh(bpy.context, mesh)
        assert temp_mesh is not mesh
        assert temp_mesh.data is not mesh.data
        assert temp_mesh in bpy.context.scene.objects.values()
        temp_mesh_data = temp_mesh.data
        bpy.data.objects.remove(temp_mesh, do_unlink=True)
        bpy.data.meshes.remove(temp_mesh_data)

        second_mesh = make_weighted_mesh("KJ_Test_Accessory", armature, "Unused")
        props = scene.batch_better_fbx_props
        props.target_armature = armature
        props.export_dir = tempfile.mkdtemp(prefix="kj-export-test-")
        props.remove_unused_bones = True
        props.mesh_list.add().obj = mesh
        props.mesh_list.add().obj = second_mesh
        EXPORT_CAPTURES.clear()
        SOURCE_OBJECT_STATES[:] = [
            (mesh, mesh.name, mesh.data),
            (second_mesh, second_mesh.name, second_mesh.data),
            (armature, armature.name, armature.data),
        ]

        assert bpy.ops.export.batch_better_fbx() == {"FINISHED"}
        assert [capture["bones"] for capture in EXPORT_CAPTURES] == [
            {"Root", "Spine", "Hand"},
            {"Root", "Unused"},
        ]
        assert all(capture["modifier_uses_export_armature"] for capture in EXPORT_CAPTURES)
        assert [capture["mesh_name"] for capture in EXPORT_CAPTURES] == [
            "KJ_Test_Mesh",
            "KJ_Test_Accessory",
        ]
        assert [capture["mesh_data_name"] for capture in EXPORT_CAPTURES] == [
            "KJ_Test_Mesh_Data",
            "KJ_Test_Accessory_Data",
        ]
        assert all(capture["source_data_unchanged"] for capture in EXPORT_CAPTURES)
        assert all(
            source.name == expected_name and source.data == expected_data
            for source, expected_name, expected_data in SOURCE_OBJECT_STATES
        )
        assert set(armature.data.bones.keys()) == {"Root", "Spine", "Hand", "Unused"}
        assert all(
            modifier.object == armature
            for source_mesh in (mesh, second_mesh)
            for modifier in source_mesh.modifiers
            if modifier.type == "ARMATURE"
        )

        # Edit-mode changes must be synchronized before the temporary mesh is
        # copied, and the user's original mode must be restored afterward.
        bpy.ops.object.select_all(action="DESELECT")
        mesh.select_set(True)
        bpy.context.view_layer.objects.active = mesh
        bpy.ops.object.mode_set(mode="EDIT")
        edit_mesh = bmesh.from_edit_mesh(mesh.data)
        edit_mesh.verts.ensure_lookup_table()
        edit_mesh.verts[0].co.x = 7.0
        props.remove_unused_bones = False
        props.force_shade_smooth = True
        while len(props.mesh_list) > 1:
            props.mesh_list.remove(len(props.mesh_list) - 1)
        EXPORT_CAPTURES.clear()

        assert bpy.ops.export.batch_better_fbx() == {"FINISHED"}
        assert len(EXPORT_CAPTURES) == 1
        assert EXPORT_CAPTURES[0]["mesh_mode"] == "OBJECT"
        assert EXPORT_CAPTURES[0]["first_vertex_x"] == 7.0
        assert mesh.mode == "EDIT"
        bpy.ops.object.mode_set(mode="OBJECT")

        # Reproduce Mesh > Sort Elements > Material and confirm the temporary
        # export mesh receives the newly sorted polygon order.
        sort_mesh = make_material_sort_mesh("KJ_Test_Material_Sort", armature)
        props.mesh_list.clear()
        props.mesh_list.add().obj = sort_mesh
        bpy.ops.object.select_all(action="DESELECT")
        sort_mesh.select_set(True)
        bpy.context.view_layer.objects.active = sort_mesh
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.sort_elements(type="MATERIAL", elements={"FACE"})
        EXPORT_CAPTURES.clear()

        assert bpy.ops.export.batch_better_fbx() == {"FINISHED"}
        assert len(EXPORT_CAPTURES) == 1
        assert EXPORT_CAPTURES[0]["polygon_material_order"] == (0, 1, 1)
        assert EXPORT_CAPTURES[0]["evaluated_polygon_material_order"] == (0, 1, 1)
        assert sort_mesh.mode == "EDIT"
        bpy.ops.object.mode_set(mode="OBJECT")
        print("KJ_EXPORT_REGISTRATION_OK")
    finally:
        addon.unregister()
        bpy.utils.unregister_class(TEST_OT_better_export_fbx)


if __name__ == "__main__":
    run()
