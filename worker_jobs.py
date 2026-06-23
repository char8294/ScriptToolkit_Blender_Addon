"""Batch implementations.  This module is only imported by worker_entry.py."""

import os

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


def _report(result, callback, message):
    result["message"] = message
    callback()


def _purge_orphans():
    try:
        bpy.data.orphans_purge(do_recursive=True)
    except Exception:
        pass


def clear_scene():
    if bpy.context.mode != "OBJECT" and bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    _purge_orphans()


def clear_scene_except(keep_objects):
    if bpy.context.mode != "OBJECT" and bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        if obj not in keep_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
    _purge_orphans()


def collect_fbx_files(folder, recursive):
    if recursive:
        return sorted(
            os.path.join(root, name)
            for root, _, names in os.walk(folder)
            for name in names if name.lower().endswith(".fbx")
        )
    return sorted(
        os.path.join(folder, name) for name in os.listdir(folder)
        if name.lower().endswith(".fbx")
    )


def output_path_for(source_path, job, name=None):
    if name is None:
        if job["keep_folder_structure"]:
            relative = os.path.relpath(source_path, job["input_dir"])
        else:
            relative = os.path.basename(source_path)
        return os.path.join(job["output_dir"], relative)
    return os.path.join(job["output_dir"], name + ".fbx")


def activate(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def clear_custom_normals(obj):
    if not getattr(obj.data, "has_custom_normals", False):
        return
    try:
        activate(obj)
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
    except Exception:
        pass


def clean_geometry(mesh_objects, options):
    for obj in mesh_objects:
        if options.get("shade_smooth"):
            for polygon in obj.data.polygons:
                polygon.use_smooth = True
        if options.get("clear_sharp_edges"):
            for edge in obj.data.edges:
                try:
                    if hasattr(edge, "use_edge_sharp"):
                        edge.use_edge_sharp = False
                    elif hasattr(edge, "use_sharp"):
                        edge.use_sharp = False
                except Exception:
                    pass
        if options.get("clear_custom_normals"):
            clear_custom_normals(obj)
        obj.data.update()


def apply_scene_scale():
    objects = list(bpy.context.scene.objects)
    if not objects:
        return
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    try:
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    except Exception:
        # Some object types cannot be applied together.  Apply eligible objects one by one.
        for obj in objects:
            try:
                activate(obj)
                bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
            except Exception:
                pass


def export_all(output_path, disable_leaf_bones, apply_scale=False):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    kwargs = {
        "filepath": output_path,
        "use_selection": False,
        "add_leaf_bones": not disable_leaf_bones,
    }
    if apply_scale:
        kwargs["apply_scale_options"] = "FBX_SCALE_ALL"
    bpy.ops.export_scene.fbx(**kwargs)


def export_related(armature, meshes, output_path, disable_leaf_bones):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.export_scene.fbx(
        filepath=output_path,
        use_selection=True,
        apply_unit_scale=False,
        apply_scale_options="FBX_SCALE_NONE",
        bake_space_transform=False,
        add_leaf_bones=not disable_leaf_bones,
        path_mode="AUTO",
    )


def _handle_file_result(result, source_path, output_path, job, operation):
    if os.path.isfile(output_path) and not job["overwrite_existing"]:
        result.setdefault("skipped", []).append({"file": source_path, "reason": "Output already exists"})
        return False
    operation()
    result["success"] += 1
    return True


def run_reexport(job, result, callback):
    files = collect_fbx_files(job["input_dir"], job["include_subfolders"])
    result["total"] = len(files)
    result.setdefault("skipped", [])
    options = job["reexport"]
    for index, source in enumerate(files, 1):
        output = output_path_for(source, job)
        _report(result, callback, f"[{index}/{len(files)}] Re-export: {os.path.basename(source)}")
        try:
            def operation():
                clear_scene()
                bpy.ops.import_scene.fbx(filepath=source)
                meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
                clean_geometry(meshes, options)
                if options.get("apply_scale"):
                    apply_scene_scale()
                export_all(output, job["disable_leaf_bones"], options.get("apply_scale", False))
            _handle_file_result(result, source, output, job, operation)
        except Exception as exc:
            result["failed"].append({"file": source, "error": str(exc)})
        callback()


def remove_empty_objects():
    for obj in list(bpy.context.scene.objects):
        if obj.type == "EMPTY":
            bpy.data.objects.remove(obj, do_unlink=True)


def used_bone_names(mesh_objects):
    names = set()
    for obj in mesh_objects:
        groups_by_index = {group.index: group.name for group in obj.vertex_groups}
        for vertex in obj.data.vertices:
            for group in vertex.groups:
                if group.weight > 0 and group.group in groups_by_index:
                    names.add(groups_by_index[group.group])
    return names


def remove_unused_bones(armature, mesh_objects):
    names = used_bone_names(mesh_objects)
    try:
        activate(armature)
        bpy.ops.object.mode_set(mode="EDIT")
        for bone in list(armature.data.edit_bones):
            if bone.name not in names:
                armature.data.edit_bones.remove(bone)
    finally:
        if bpy.context.mode != "OBJECT" and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")


def move_keyword_material_to_slot_zero(obj, keyword):
    keyword = keyword.lower()
    source_index = next((
        index for index, slot in enumerate(obj.material_slots)
        if slot.material and keyword in slot.material.name.lower()
    ), -1)
    if source_index <= 0:
        return source_index == 0
    for polygon in obj.data.polygons:
        if polygon.material_index == 0:
            polygon.material_index = source_index
        elif polygon.material_index == source_index:
            polygon.material_index = 0
    first = obj.material_slots[0].material
    target = obj.material_slots[source_index].material
    obj.material_slots[0].material = target
    obj.material_slots[source_index].material = first
    obj.data.update()
    return True


def sort_faces_by_material(obj):
    try:
        activate(obj)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.sort_elements(type="MATERIAL", elements={"FACE"})
    except Exception:
        pass
    finally:
        if bpy.context.mode != "OBJECT" and bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")


def run_material_cleanup(job, result, callback):
    files = collect_fbx_files(job["input_dir"], job["include_subfolders"])
    result["total"] = len(files)
    result.setdefault("skipped", [])
    options = job["material_cleanup"]
    geometry_options = {
        "shade_smooth": options["shade_smooth"],
        "clear_sharp_edges": options["clear_sharp_edges"],
        "clear_custom_normals": options["clear_custom_normals"],
    }
    for index, source in enumerate(files, 1):
        output = output_path_for(source, job)
        _report(result, callback, f"[{index}/{len(files)}] Material cleanup: {os.path.basename(source)}")
        try:
            def operation():
                clear_scene()
                bpy.ops.import_scene.fbx(filepath=source)
                if options["remove_empty"]:
                    remove_empty_objects()
                meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
                if options["remove_unused_bones"]:
                    for armature in [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]:
                        remove_unused_bones(armature, meshes)
                for obj in meshes:
                    if options["set_slot_zero"]:
                        move_keyword_material_to_slot_zero(obj, options["material_keyword"])
                    if options["sort_faces"]:
                        sort_faces_by_material(obj)
                clean_geometry(meshes, geometry_options)
                export_all(output, job["disable_leaf_bones"])
            _handle_file_result(result, source, output, job, operation)
        except Exception as exc:
            result["failed"].append({"file": source, "error": str(exc)})
        callback()


def clean_name_string(name):
    def remove_blender_number(value):
        head, separator, tail = value.rpartition(".")
        return head if separator and tail.isdigit() else value
    name = remove_blender_number(name).strip("_").strip()
    name = name.replace("_mat", "").replace("_MAT", "")
    return remove_blender_number(name).strip("_").strip()


def clear_mesh_edge_data(obj):
    for edge in obj.data.edges:
        for attribute, value in (("use_edge_sharp", False), ("use_sharp", False), ("use_seam", False), ("crease", 0.0), ("bevel_weight", 0.0)):
            try:
                if hasattr(edge, attribute):
                    setattr(edge, attribute, value)
            except Exception:
                pass
    for name in ("crease_edge", "bevel_weight_edge", "crease_vert", "bevel_weight_vert"):
        try:
            attribute = obj.data.attributes.get(name)
            if attribute:
                obj.data.attributes.remove(attribute)
        except Exception:
            pass


def separate_meshes_by_material(minimum_materials):
    original_meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    for obj in original_meshes:
        valid_count = sum(1 for slot in obj.material_slots if slot.material)
        if valid_count < minimum_materials:
            continue
        try:
            activate(obj)
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.mesh.separate(type="MATERIAL")
        finally:
            if bpy.context.mode != "OBJECT" and bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode="OBJECT")


def process_split_meshes(options):
    for obj in [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]:
        if not obj.data.polygons:
            continue
        slot_index = obj.data.polygons[0].material_index
        material = obj.material_slots[slot_index].material if slot_index < len(obj.material_slots) else None
        if material:
            name = clean_name_string(material.name) if options["clean_names"] else material.name
            if options["rename_data"]:
                material.name = name
                obj.name = name
                obj.data.name = name
            obj.data.materials.clear()
            obj.data.materials.append(material)
            for polygon in obj.data.polygons:
                polygon.material_index = 0
        elif options["rename_data"]:
            obj.name = f"{obj.name}_NoMaterial"
            obj.data.name = f"{obj.data.name}_NoMaterial"
        if options["clear_edge_marks"]:
            clear_mesh_edge_data(obj)
        obj.data.update()
    clean_geometry(
        [obj for obj in bpy.context.scene.objects if obj.type == "MESH"],
        {"shade_smooth": options["shade_smooth"], "clear_sharp_edges": False, "clear_custom_normals": options["clear_custom_normals"]},
    )


def run_separate(job, result, callback):
    files = collect_fbx_files(job["input_dir"], job["include_subfolders"])
    result["total"] = len(files)
    result.setdefault("skipped", [])
    options = job["separate"]
    for index, source in enumerate(files, 1):
        output = output_path_for(source, job)
        _report(result, callback, f"[{index}/{len(files)}] Separate: {os.path.basename(source)}")
        try:
            def operation():
                clear_scene()
                bpy.ops.import_scene.fbx(filepath=source)
                separate_meshes_by_material(options["minimum_materials"])
                process_split_meshes(options)
                export_all(output, job["disable_leaf_bones"])
            _handle_file_result(result, source, output, job, operation)
        except Exception as exc:
            result["failed"].append({"file": source, "error": str(exc)})
        callback()


def find_skin_volume(keyword):
    keyword = keyword.lower()
    return next((obj for obj in bpy.context.scene.objects if obj.type == "MESH" and keyword in obj.name.lower()), None)


def find_armature_meshes(armature_name):
    armature = bpy.data.objects.get(armature_name)
    if armature is None:
        armature = next((obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE" and armature_name in obj.name), None)
    if armature is None:
        return None, []
    meshes = []
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH" and any(mod.type == "ARMATURE" and mod.object == armature for mod in obj.modifiers):
            meshes.append(obj)
    return armature, meshes


def point_is_inside(point, bvh):
    direction = Vector((0.0, 0.0, 1.0))
    origin = point.copy()
    hits = 0
    while hits < 100000:
        location, _, face_index, _ = bvh.ray_cast(origin, direction)
        if face_index is None:
            break
        hits += 1
        origin = location + direction * 0.00001
    return hits % 2 == 1


def normalize_vertex_weights(obj, target_group_name):
    target = obj.vertex_groups.get(target_group_name)
    if target is None:
        return
    groups_by_index = {group.index: group for group in obj.vertex_groups}
    for vertex in obj.data.vertices:
        weights = {item.group: item.weight for item in vertex.groups}
        target_weight = weights.get(target.index, 0.0)
        if target_weight <= 0:
            continue
        others = {index: weight for index, weight in weights.items() if index != target.index}
        total = sum(others.values())
        if total <= 0:
            continue
        scale = max(0.0, 1.0 - target_weight) / total
        for index, weight in others.items():
            groups_by_index[index].add([vertex.index], weight * scale, "REPLACE")


def apply_overlap_weight(obj, volume_inverse_matrix, bvh, options):
    group = obj.vertex_groups.get(options["target_vertex_group"])
    if group is None:
        group = obj.vertex_groups.new(name=options["target_vertex_group"])
    inside = []
    for vertex in obj.data.vertices:
        world_point = obj.matrix_world @ vertex.co
        volume_local_point = volume_inverse_matrix @ world_point
        if point_is_inside(volume_local_point, bvh):
            inside.append(vertex.index)
    if inside:
        group.add(inside, options["weight_value"], "REPLACE")
        if options["auto_normalize"]:
            normalize_vertex_weights(obj, options["target_vertex_group"])
        obj.data.update()
    return len(inside)


def strip_blender_number(name):
    head, separator, tail = name.rpartition(".")
    return head if separator and tail.isdigit() else name


def run_overlap(job, result, callback):
    files = collect_fbx_files(job["input_dir"], job["include_subfolders"])
    result["total"] = len(files)
    result.setdefault("skipped", [])
    options = job["overlap"]
    clear_scene()
    bpy.ops.import_scene.fbx(filepath=options["skin_volume_path"])
    skin_objects = set(bpy.context.scene.objects)
    volume = find_skin_volume(options["skin_volume_keyword"])
    if volume is None:
        raise RuntimeError(f"SkinVolume object containing '{options['skin_volume_keyword']}' was not found")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    volume_evaluated = volume.evaluated_get(depsgraph)
    bvh = BVHTree.FromObject(volume_evaluated, depsgraph)
    volume_inverse_matrix = volume.matrix_world.inverted()

    for index, source in enumerate(files, 1):
        _report(result, callback, f"[{index}/{len(files)}] Skin Volume: {os.path.basename(source)}")
        try:
            bpy.ops.import_scene.fbx(filepath=source)
            armature, meshes = find_armature_meshes(options["armature_name"])
            if armature is None or not meshes:
                result.setdefault("skipped", []).append({"file": source, "reason": "Armature or bound meshes not found"})
                clear_scene_except(skin_objects)
                callback()
                continue
            if options["output_name"] == "MESH":
                output = output_path_for(source, job, strip_blender_number(meshes[0].name))
            else:
                output = output_path_for(source, job)
            if os.path.isfile(output) and not job["overwrite_existing"]:
                result.setdefault("skipped", []).append({"file": source, "reason": "Output already exists"})
                clear_scene_except(skin_objects)
                callback()
                continue
            vertices_changed = sum(apply_overlap_weight(mesh, volume_inverse_matrix, bvh, options) for mesh in meshes)
            export_related(armature, meshes, output, job["disable_leaf_bones"])
            result["success"] += 1
            result.setdefault("details", []).append({"file": source, "weighted_vertices": vertices_changed})
        except Exception as exc:
            result["failed"].append({"file": source, "error": str(exc)})
        clear_scene_except(skin_objects)
        callback()


def run_job(job, result, callback):
    tool = job.get("tool")
    dispatch = {
        "REEXPORT": run_reexport,
        "OVERLAP": run_overlap,
        "MATERIAL_CLEANUP": run_material_cleanup,
        "SEPARATE": run_separate,
    }
    if tool not in dispatch:
        raise RuntimeError(f"Unsupported batch tool: {tool}")
    dispatch[tool](job, result, callback)
