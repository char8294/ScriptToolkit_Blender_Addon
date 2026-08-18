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
    addon.register()

    assert hasattr(bpy.types.Scene, "fbx_ignore_armature_node")
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
    assert io_scene_fbx.export_panel_armature is feature._patched_export_panel_armature
    assert io_scene_fbx.ImportFBX.execute is feature._patched_import_execute

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

    addon.unregister()
    assert not hasattr(bpy.types.Scene, "fbx_universal_root_enabled")
    print("FBX_IMPORT_EXPORT_PATCH_OK")


if __name__ == "__main__":
    run()
