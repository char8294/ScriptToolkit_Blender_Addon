"""Blender background checks for the native FBX import/export extensions."""

import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy
import io_scene_fbx


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


def run():
    addon = load_addon()
    feature = addon.fbx_import_export
    better_feature = addon.better_fbx_export
    addon.register()

    assert hasattr(bpy.types.Scene, "fbx_ignore_armature_node")
    assert hasattr(bpy.types.Scene, "better_fbx_bake_mesh_transforms")
    assert hasattr(bpy.types.Scene, "fbx_universal_root_enabled")
    assert feature._ORIG
    assert io_scene_fbx.export_panel_armature is feature._patched_export_panel_armature
    assert io_scene_fbx.ImportFBX.execute is feature._patched_import_execute
    assert hasattr(bpy.types, "FILEBROWSER_PT_script_toolkit_fbx")
    assert feature.FILEBROWSER_PT_script_toolkit_fbx.bl_parent_id == "FILE_PT_operator"
    fake_import_context = SimpleNamespace(
        space_data=SimpleNamespace(
            active_operator=SimpleNamespace(
                bl_rna=SimpleNamespace(identifier="WM_OT_fbx_import")
            )
        )
    )
    assert feature.FILEBROWSER_PT_script_toolkit_fbx.poll(fake_import_context)
    fake_c_import_context = SimpleNamespace(
        space_data=SimpleNamespace(
            active_operator=SimpleNamespace(
                bl_idname="WM_OT_fbx_import",
                bl_rna=SimpleNamespace(identifier="Operator"),
            )
        )
    )
    assert feature.FILEBROWSER_PT_script_toolkit_fbx.poll(fake_c_import_context)
    fake_better_export_context = SimpleNamespace(
        space_data=SimpleNamespace(
            active_operator=SimpleNamespace(
                bl_idname="BETTER_EXPORT_OT_fbx",
                bl_rna=SimpleNamespace(identifier="Operator"),
            )
        )
    )
    assert not feature.FILEBROWSER_PT_script_toolkit_fbx.poll(fake_better_export_context)
    assert better_feature.FILEBROWSER_PT_script_toolkit_better_fbx.poll(
        fake_better_export_context
    )
    if better_feature._BETTER is not None:
        assert better_feature._better_patch_is_active()

    bake_arm_data = bpy.data.armatures.new("BetterBakeTestArmature")
    bake_arm = bpy.data.objects.new("BetterBakeTestArmature", bake_arm_data)
    bpy.context.collection.objects.link(bake_arm)
    bake_mesh_data = bpy.data.meshes.new("BetterBakeTestMesh")
    bake_mesh_data.from_pydata(
        [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)],
        [],
        [(0, 1, 2)],
    )
    bake_mesh = bpy.data.objects.new("BetterBakeTestMesh", bake_mesh_data)
    bpy.context.collection.objects.link(bake_mesh)
    bake_mesh.parent = bake_arm
    bake_mesh.vertex_groups.new(name="root")
    bake_modifier = bake_mesh.modifiers.new(name="Armature", type="ARMATURE")
    bake_modifier.object = bake_arm
    original_bake_data = bake_mesh.data
    original_bake_basis = bake_mesh.matrix_basis.copy()
    original_bake_vertex = bake_mesh.data.vertices[1].co.copy()
    bake_state = better_feature._bake_better_mesh_transforms((bake_mesh,))
    assert round(bake_mesh.rotation_euler.x, 6) == round(3.141592653589793 / 2, 6)
    assert tuple(round(value, 6) for value in bake_mesh.scale) == (1.0, 1.0, 1.0)
    assert bake_mesh.data != original_bake_data
    assert bake_mesh.data.vertices[1].co != original_bake_vertex
    better_feature._restore_better_mesh_transforms(bake_state)
    assert bake_mesh.data == original_bake_data
    assert bake_mesh.matrix_basis == original_bake_basis
    bpy.data.objects.remove(bake_mesh, do_unlink=True)
    bpy.data.meshes.remove(bake_mesh_data)
    bpy.data.objects.remove(bake_arm, do_unlink=True)
    bpy.data.armatures.remove(bake_arm_data)

    io_scene_fbx.export_panel_armature = feature._ORIG["export_panel_armature"]
    io_scene_fbx.ImportFBX.execute = feature._ORIG["import_execute"]
    feature._EXPORT.save = feature._ORIG["save"]
    feature._EXPORT.fbx_animations_do = feature._ORIG["fbx_animations_do"]
    feature._EXPORT.fbx_data_empty_elements = feature._ORIG["fbx_data_empty_elements"]
    feature._EXPORT.fbx_data_object_elements = feature._ORIG["fbx_data_object_elements"]
    feature._EXPORT.fbx_data_bindpose_element = feature._ORIG["fbx_data_bindpose_element"]
    feature._EXPORT.fbx_data_from_scene = feature._ORIG["fbx_data_from_scene"]
    feature._UTILS.ObjectWrapper.fbx_object_matrix = feature._ORIG["fbx_object_matrix"]
    feature._patch_retry_timer()
    fake_export_context = SimpleNamespace(
        space_data=SimpleNamespace(
            active_operator=SimpleNamespace(
                bl_idname="EXPORT_SCENE_OT_fbx",
                bl_rna=SimpleNamespace(identifier="Operator"),
            )
        )
    )
    assert feature.FILEBROWSER_PT_script_toolkit_fbx.poll(fake_export_context)
    assert feature._patch_is_active()
    assert io_scene_fbx.export_panel_armature is feature._patched_export_panel_armature
    assert io_scene_fbx.ImportFBX.execute is feature._patched_import_execute
    assert feature._EXPORT.save is feature._patched_save
    assert feature._EXPORT.fbx_data_from_scene is feature._patched_fbx_data_from_scene

    arm_data = bpy.data.armatures.new("UniversalTestArmature")
    arm_obj = bpy.data.objects.new("UniversalTestArmature", arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    pelvis = arm_data.edit_bones.new("Pelvis")
    pelvis.head = (0.0, 0.0, 0.0)
    pelvis.tail = (0.0, 1.0, 0.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    arm_obj.location.x = 0.0
    arm_obj.keyframe_insert(data_path="location", frame=1.0, index=0)
    arm_obj.location.x = 1.0
    arm_obj.keyframe_insert(data_path="location", frame=10.0, index=0)
    action = arm_obj.animation_data.action
    curve = action.layers[0].strips[0].channelbags[0].fcurves[0]
    scene = bpy.context.scene
    scene.fbx_universal_root_enabled = True
    scene.fbx_universal_root_mode = "AUTO"
    success, _message = feature._process_armature_in_context(arm_obj, bpy.context)
    assert success
    assert arm_data.bones.get("root") is not None
    assert arm_data.bones.get("Pelvis").parent.name == "root"
    assert curve.data_path == 'pose.bones["root"].location'

    source_data = bpy.data.armatures.new("FBXSourceArmature")
    source_obj = bpy.data.objects.new("FBXSourceArmature", source_data)
    bpy.context.collection.objects.link(source_obj)
    bpy.context.view_layer.objects.active = source_obj
    source_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    hips = source_data.edit_bones.new("Hips")
    hips.head = (0.0, 0.0, 0.0)
    hips.tail = (0.0, 1.0, 0.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    export_path = Path(tempfile.gettempdir()) / "script_toolkit_fbx_import_export_test.fbx"
    bpy.ops.object.select_all(action="DESELECT")
    source_obj.select_set(True)
    bpy.context.view_layer.objects.active = source_obj
    scene.fbx_ignore_armature_node = True
    export_result = bpy.ops.export_scene.fbx(
        filepath=str(export_path),
        use_selection=True,
        add_leaf_bones=False,
    )
    assert export_result == {"FINISHED"}
    assert export_path.exists()
    export_bytes = export_path.read_bytes()
    assert source_obj.name.encode() not in export_bytes

    scene.fbx_ignore_armature_node = False
    ordinary_export_path = Path(tempfile.gettempdir()) / "script_toolkit_fbx_ordinary_export_test.fbx"
    ordinary_export_result = bpy.ops.export_scene.fbx(
        filepath=str(ordinary_export_path),
        use_selection=True,
        add_leaf_bones=False,
    )
    assert ordinary_export_result == {"FINISHED"}
    assert source_obj.name.encode() in ordinary_export_path.read_bytes()

    scene.fbx_universal_root_enabled = True
    feature._KNOWN_OBJECT_NAMES = set(bpy.data.objects.keys())
    feature._PENDING_IMPORT_NAMES.clear()
    feature._PENDING_IMPORT_RETRIES.clear()
    import_result = bpy.ops.wm.fbx_import(filepath=str(export_path))
    assert import_result == {"FINISHED"}
    feature._depsgraph_update_post(scene, None)
    for _index in range(5):
        feature._process_pending_imports_timer()
    imported_armatures = [
        obj for obj in bpy.data.objects if obj.type == "ARMATURE" and obj != arm_obj and obj != source_obj
    ]
    assert imported_armatures
    assert any(bone.name == "root" for bone in imported_armatures[0].data.bones)
    export_path.unlink(missing_ok=True)
    ordinary_export_path.unlink(missing_ok=True)

    addon.unregister()
    assert not hasattr(bpy.types.Scene, "fbx_universal_root_enabled")
    assert not hasattr(bpy.types.Scene, "better_fbx_bake_mesh_transforms")
    print("FBX_IMPORT_EXPORT_PATCH_OK")


if __name__ == "__main__":
    run()
