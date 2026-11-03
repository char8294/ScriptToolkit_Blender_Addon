"""Hierarchy preview and temporary FBX export implementation."""

import math
import os
import re
import traceback

import bpy
from bpy_extras import anim_utils

PREVIEW_COLLECTION_NAME = "REX_PREVIEW"
EXPORT_TEMP_COLLECTION_NAME = "REX_EXPORT_TEMP"

_BETTER_FBX_PRESET_ITEMS = []


def better_fbx_available():
    """Return whether Better FBX is registered in Blender."""
    try:
        bpy.ops.better_export.fbx.get_rna_type()
        return True
    except (AttributeError, KeyError, RuntimeError, TypeError):
        return False


def _better_fbx_preset_directory():
    appdata = os.getenv("APPDATA")
    if appdata:
        return os.path.join(
            appdata,
            "Blender Foundation",
            "Blender",
            f"{bpy.app.version[0]}.{bpy.app.version[1]}",
            "scripts",
            "presets",
            "operator",
            "better_export.fbx",
        )
    return bpy.utils.user_resource(
        "SCRIPTS", path=os.path.join("presets", "operator", "better_export.fbx")
    ) or ""


def better_fbx_preset_items(_self=None, _context=None):
    """Build the Preset dropdown from Better FBX's installed preset folder."""
    del _self, _context
    _BETTER_FBX_PRESET_ITEMS.clear()
    _BETTER_FBX_PRESET_ITEMS.append(("NONE", "No Preset", "Use Better FBX defaults"))
    preset_directory = _better_fbx_preset_directory()
    try:
        preset_names = sorted(
            filename[:-3]
            for filename in os.listdir(preset_directory)
            if filename.lower().endswith(".py")
        )
    except OSError:
        preset_names = []
    _BETTER_FBX_PRESET_ITEMS.extend(
        (name, name, f"Use the Better FBX {name} preset")
        for name in preset_names
    )
    return _BETTER_FBX_PRESET_ITEMS


def _better_fbx_animation_types():
    """Return Better FBX animation modes for validating the fixed export modes."""
    try:
        operator = bpy.ops.better_export.fbx.get_rna_type()
        enum_property = operator.properties.get("my_animation_type")
        if enum_property:
            return {item.identifier for item in enum_property.enum_items}
    except (AttributeError, KeyError, RuntimeError, TypeError):
        pass
    return {"Active", "Actions", "Tracks"}


def _load_better_fbx_preset(preset_name):
    if not preset_name or preset_name == "NONE":
        return {}
    preset_directory = _better_fbx_preset_directory()
    preset_path = os.path.join(preset_directory, preset_name + ".py")
    if not os.path.isfile(preset_path):
        raise RuntimeError(f"Better FBX preset {preset_name!r} was not found.")

    class MockOperator:
        pass

    mock_operator = MockOperator()
    with open(preset_path, "r", encoding="utf-8") as preset_file:
        source = preset_file.read().replace(
            "op = bpy.context.active_operator", "op = mock_operator"
        )
    exec(compile(source, preset_path, "exec"), {"bpy": bpy, "mock_operator": mock_operator})

    try:
        valid_properties = {
            prop.identifier
            for prop in bpy.ops.better_export.fbx.get_rna_type().properties
        }
    except (AttributeError, KeyError, RuntimeError, TypeError):
        valid_properties = set()
    return {
        name: getattr(mock_operator, name)
        for name in dir(mock_operator)
        if not name.startswith("__")
        and name != "filepath"
        and (not valid_properties or name in valid_properties)
    }


def _better_fbx_export(
    filepath, profile, use_animation, animation_type_override=None
):
    if not better_fbx_available():
        raise RuntimeError("Better FBX is not enabled. Enable the Better FBX add-on and try again.")

    export_kwargs = _load_better_fbx_preset(
        getattr(profile, "better_fbx_preset", "NONE")
    )
    animation_type = animation_type_override or "Active"
    animation_types = _better_fbx_animation_types()
    if animation_type not in animation_types:
        raise RuntimeError(
            f"Better FBX does not support Animation Type {animation_type!r}."
        )
    operator_properties = {
        prop.identifier for prop in bpy.ops.better_export.fbx.get_rna_type().properties
    }
    export_kwargs.update(
        filepath=filepath,
        my_file_type=".fbx",
        use_selection=True,
        use_active_collection=False,
        use_visible=False,
        use_separate_files=False,
        use_separate_collection=False,
        use_animation=bool(use_animation),
        my_animation_type=animation_type,
    )
    if animation_type_override == "Tracks":
        export_kwargs["use_concatenate_all"] = False
        export_kwargs["use_timeline_range"] = False
        if "use_driver_based_shape_key" in operator_properties:
            # Packed Actions already provide explicit Key-slot NLA tracks.
            # Better FBX's driver bake otherwise adds duplicate generic
            # "baked shape key" stacks for every armature NLA track.
            export_kwargs["use_driver_based_shape_key"] = False
    # Better FBX applies evaluated mesh modifiers before it writes the skin.
    # Keep the Armature modifier as the skin binding only; applying its current
    # pose here bakes the mesh pose into the vertices and the exported animation
    # deforms those vertices a second time on import.
    if "use_include_armature_deform_modifier" in operator_properties:
        export_kwargs["use_include_armature_deform_modifier"] = False
    return bpy.ops.better_export.fbx(**export_kwargs)


def _set_action_as_nla_track(
    export_rig, action, action_name, frame_start, action_slot=None
):
    """Add a baked armature Action as its own NLA track."""
    animation_data = export_rig.animation_data_create()
    if action_slot is None:
        action_slot = getattr(animation_data, "action_slot", None)
    animation_data.action = None
    track = animation_data.nla_tracks.new()
    track.name = action_name
    strip = track.strips.new(action_name, frame_start, action)
    if action_slot and hasattr(strip, "action_slot"):
        strip.action_slot = action_slot
    animation_data.use_nla = True


def _set_shape_key_as_nla_track(
    key_data, action, action_name, frame_bounds, action_slot=None
):
    """Add a shape-key Action as its own NLA track."""
    animation_data = key_data.animation_data_create()
    track = animation_data.nla_tracks.new()
    track.name = action_name
    frame_start, frame_end = frame_bounds
    strip = track.strips.new(action_name, frame_start, action)
    strip.action_frame_start = frame_start
    strip.action_frame_end = frame_end
    strip.frame_start = frame_start
    strip.frame_end = frame_end + 1
    if action_slot and hasattr(strip, "action_slot"):
        strip.action_slot = action_slot
    animation_data.use_nla = True


def _unique_nla_track_name(name, used_names):
    base_name = _safe_name(name)
    candidate = base_name
    suffix = 2
    while candidate in used_names:
        candidate = f"{base_name}_{suffix}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def is_rigify_rig(obj):
    return bool(
        obj
        and obj.type == "ARMATURE"
        and obj.data
        and obj.data.get("rig_id")
    )


def source_rig_for_object(obj):
    """Resolve a selected Rigify armature or one of its tagged previews."""
    if is_rigify_rig(obj):
        return obj
    if not obj or obj.type != "ARMATURE":
        return None
    for rig in bpy.data.objects:
        if is_rigify_rig(rig) and any(
            profile.preview_object == obj for profile in getattr(rig, "rex_profiles", ())
        ):
            return rig
    source = bpy.data.objects.get(obj.get("rex_preview_source", ""))
    return source if is_rigify_rig(source) else None


def move_preview_to_collection(preview_rig, scene=None):
    """Place an add-on preview in a dedicated scene collection."""
    if not preview_rig:
        return None
    if scene is None:
        scene = next(iter(preview_rig.users_scene), None) or bpy.context.scene
    if not scene:
        return None

    collection = bpy.data.collections.get(PREVIEW_COLLECTION_NAME)
    if not collection:
        collection = bpy.data.collections.new(PREVIEW_COLLECTION_NAME)
    if not any(child == collection for child in scene.collection.children):
        scene.collection.children.link(collection)

    if not any(obj == preview_rig for obj in collection.objects):
        collection.objects.link(preview_rig)
    for previous_collection in tuple(preview_rig.users_collection):
        if previous_collection != collection:
            previous_collection.objects.unlink(preview_rig)
    return collection


def cleanup_empty_preview_collection():
    """Remove the preview collection after its last rig has been deleted."""
    collection = bpy.data.collections.get(PREVIEW_COLLECTION_NAME)
    if (
        not collection
        or len(collection.objects) > 0
        or len(collection.children) > 0
    ):
        return False
    bpy.data.collections.remove(collection, do_unlink=True)
    return True


def _export_temp_collection(scene):
    """Get the scene collection used to contain temporary export objects."""
    collection = bpy.data.collections.get(EXPORT_TEMP_COLLECTION_NAME)
    if not collection:
        collection = bpy.data.collections.new(EXPORT_TEMP_COLLECTION_NAME)
    if not any(child == collection for child in scene.collection.children):
        scene.collection.children.link(collection)
    return collection


def _cleanup_empty_export_collection():
    """Remove the export collection once it no longer contains temporary data."""
    collection = bpy.data.collections.get(EXPORT_TEMP_COLLECTION_NAME)
    if (
        not collection
        or len(collection.objects) > 0
        or len(collection.children) > 0
    ):
        return False
    bpy.data.collections.remove(collection, do_unlink=True)
    return True


def _link_export_object(collection, obj):
    if not any(existing == obj for existing in collection.objects):
        collection.objects.link(obj)
    for previous_collection in tuple(obj.users_collection):
        if previous_collection != collection:
            previous_collection.objects.unlink(obj)


def _ancestor_chain(bone):
    parent = bone.parent
    while parent:
        yield parent
        parent = parent.parent


def _nearest_deform_parent(bone, bones_by_name, hierarchy_mode):
    for ancestor in _ancestor_chain(bone):
        if ancestor.use_deform:
            return ancestor.name

        if hierarchy_mode == "RIGIFY" and ancestor.name.startswith("ORG-"):
            counterpart = bones_by_name.get("DEF-" + ancestor.name[4:])
            if counterpart and counterpart.use_deform and counterpart.name != bone.name:
                return counterpart.name
    return ""


def hierarchy_map(rig, hierarchy_mode):
    """Return deform-bone names mapped to their post-extraction parents.

    Game Rig Tools' extraction deletes non-deform bones after editing the
    hierarchy. Keeping a deform bone's closest surviving deform ancestor
    reproduces Blender's reparenting when those in-between bones are removed.
    """
    bones = list(rig.data.bones)
    by_name = {bone.name: bone for bone in bones}
    deform_bones = [bone for bone in bones if bone.use_deform]
    result = {}
    for bone in deform_bones:
        if hierarchy_mode == "FLAT":
            result[bone.name] = ""
        elif bone.parent and bone.parent.use_deform:
            result[bone.name] = bone.parent.name
        else:
            result[bone.name] = _nearest_deform_parent(
                bone, by_name, hierarchy_mode
            )
    return deform_bones, result


def hierarchy_signature(rig, hierarchy_mode, disconnect_bones):
    deform_bones, parents = hierarchy_map(rig, hierarchy_mode)
    return "|".join(
        [hierarchy_mode, str(bool(disconnect_bones))]
        + [
            f"{bone.name}>{parents[bone.name]}:{int(bone.use_connect)}"
            for bone in deform_bones
        ]
        + [
            f"ALL:{bone.name}>{bone.parent.name if bone.parent else ''}:{int(bone.use_deform)}"
            for bone in rig.data.bones
        ]
    )


def hierarchy_rows(rig, hierarchy_mode):
    deform_bones, parents = hierarchy_map(rig, hierarchy_mode)
    source_order = {bone.name: i for i, bone in enumerate(deform_bones)}
    children = {bone.name: [] for bone in deform_bones}
    roots = []
    for bone in deform_bones:
        parent_name = parents[bone.name]
        if parent_name and parent_name in children:
            children[parent_name].append(bone.name)
        else:
            roots.append(bone.name)

    key = lambda name: source_order[name]
    rows = []

    def visit(name, depth):
        rows.append((name, parents[name], depth))
        for child in sorted(children[name], key=key):
            visit(child, depth + 1)

    for root in sorted(roots, key=key):
        visit(root, 0)
    return rows


def export_bone_map(profile):
    bones = list(getattr(profile, "export_bones", ()))
    names = {bone.name for bone in bones}
    return [bone.name for bone in bones], {
        bone.name: bone.parent_name if bone.parent_name in names else ""
        for bone in bones
    }


def export_bones_from_preview(preview_rig):
    deform_bones = [bone for bone in preview_rig.data.bones if bone.use_deform]
    deform_names = {bone.name for bone in deform_bones}
    rows = []
    for bone in deform_bones:
        parent = bone.parent
        while parent and parent.name not in deform_names:
            parent = parent.parent
        rows.append((bone.name, parent.name if parent else ""))
    return rows


def _edit_deform_hierarchy(context, working_rig, source_rig, parents, bone_names, disconnect_bones):
    names = set(bone_names)
    source_bones = {bone.name: bone for bone in source_rig.data.bones}
    bpy.ops.object.select_all(action="DESELECT")
    working_rig.select_set(True)
    context.view_layer.objects.active = working_rig
    build_collection = working_rig.data.collections.new("REX_BUILD_TEMP")
    try:
        for bone_name in names:
            bone = working_rig.data.bones.get(bone_name)
            if bone:
                build_collection.assign(bone)

        bpy.ops.object.mode_set(mode="EDIT")
        edit_bones = working_rig.data.edit_bones

        for bone_name in names:
            edit_bone = edit_bones.get(bone_name)
            if not edit_bone:
                continue
            parent_name = parents.get(bone_name, "")
            edit_bone.parent = edit_bones.get(parent_name) if parent_name in names else None
            edit_bone.bbone_segments = 0
            edit_bone.use_inherit_rotation = True
            edit_bone.inherit_scale = "FULL"
            edit_bone.use_local_location = True
            edit_bone.use_connect = False

        if not disconnect_bones:
            for bone_name in names:
                edit_bone = edit_bones.get(bone_name)
                source_bone = source_bones.get(bone_name)
                parent_name = parents.get(bone_name, "")
                parent = edit_bones.get(parent_name) if parent_name in names else None
                if (
                    edit_bone
                    and source_bone
                    and source_bone.use_connect
                    and parent
                    and (parent.tail - edit_bone.head).length < 1e-5
                ):
                    edit_bone.use_connect = True

        for edit_bone in list(edit_bones):
            if edit_bone.name not in names:
                edit_bones.remove(edit_bone)
    finally:
        if working_rig.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        working_rig.data.collections.remove(build_collection)

    # Rigify commonly keeps the DEF collection hidden on the control armature.
    # The copied Game Rig must expose its remaining deform bones in the viewport.
    for collection in working_rig.data.collections:
        collection.is_visible = True
    for bone in working_rig.data.bones:
        bone.hide = False


def remove_preview_object(profile):
    preview = profile.preview_object
    source_rig = profile.source_rig
    if not source_rig and preview:
        source_rig = bpy.data.objects.get(preview.get("rex_preview_source", ""))
    if preview and preview.mode != "OBJECT":
        active = bpy.context.view_layer.objects.active
        bpy.context.view_layer.objects.active = preview
        bpy.ops.object.mode_set(mode="OBJECT")
        if active and bpy.data.objects.get(active.name) == active:
            bpy.context.view_layer.objects.active = active
    data = preview.data if preview else None
    profile.preview_object = None
    if getattr(profile, "game_rig", None) == preview:
        profile.game_rig = None
    if preview:
        bpy.data.objects.remove(preview, do_unlink=True)
        if data and data.users == 0 and isinstance(data, bpy.types.Armature):
            bpy.data.armatures.remove(data)
    if source_rig and source_rig.name in bpy.data.objects:
        source_rig.hide_set(profile.preview_source_was_hidden)
    profile.source_rig = None
    profile.preview_source_was_hidden = False
    cleanup_empty_preview_collection()


def make_preview_visible(preview_rig):
    """Show a copied Game Rig even when its source DEF collection was hidden."""
    if not preview_rig:
        return
    move_preview_to_collection(preview_rig)
    preview_rig.hide_viewport = False
    preview_rig.hide_set(False)
    preview_rig.hide_render = False
    preview_rig.display_type = "SOLID"
    preview_rig.show_in_front = True
    for collection in preview_rig.data.collections:
        collection.is_visible = True
    for bone in preview_rig.data.bones:
        bone.hide = False


def ensure_preview_constraints(preview_rig, source_rig):
    """Make the temporary Game Rig follow matching control-rig DEF bones."""
    if not preview_rig or not source_rig:
        return
    for pose_bone in preview_rig.pose.bones:
        if source_rig.pose.bones.get(pose_bone.name) is None:
            continue
        for constraint_type, constraint_name in (
            ("COPY_LOCATION", "REX Preview Copy Location"),
            ("COPY_ROTATION", "REX Preview Copy Rotation"),
        ):
            existing = next(
                (
                    constraint
                    for constraint in pose_bone.constraints
                    if constraint.type == constraint_type
                    and constraint.target == source_rig
                    and constraint.subtarget == pose_bone.name
                ),
                None,
            )
            if existing:
                continue
            constraint = pose_bone.constraints.new(constraint_type)
            constraint.name = constraint_name
            constraint.target = source_rig
            constraint.subtarget = pose_bone.name


def create_preview_rig(context, source_rig, profile):
    """Create an editable, temporary deform-only copy for bone review."""
    deform_bones, parents = hierarchy_map(source_rig, profile.hierarchy_mode)
    bone_names = [bone.name for bone in deform_bones]
    if not bone_names:
        raise ValueError("This Rigify rig has no deform bones to preview.")

    remove_preview_object(profile)
    if source_rig.mode != "OBJECT":
        context.view_layer.objects.active = source_rig
        bpy.ops.object.mode_set(mode="OBJECT")

    preview = source_rig.copy()
    armature = source_rig.data.copy()
    preview.data = armature
    preview.name = "REX_PREVIEW_" + _safe_name(profile.name)
    armature.name = preview.name + "_Armature"
    try:
        move_preview_to_collection(preview, context.scene)
        source_matrix = source_rig.matrix_world.copy()
        preview.parent = None
        preview.matrix_world = source_matrix
        preview.animation_data_clear()
        armature.animation_data_clear()
        preview.id_properties_clear()
        armature.id_properties_clear()
        preview["rex_preview_source"] = source_rig.name
        preview["rex_preview_profile_uid"] = profile.uid
        profile.control_rig = source_rig
        profile.source_rig = source_rig
        profile.preview_source_was_hidden = source_rig.hide_get()
        make_preview_visible(preview)
        source_rig.hide_set(True)
        for constraint in preview.constraints[:]:
            preview.constraints.remove(constraint)
        for pose_bone in preview.pose.bones:
            for constraint in pose_bone.constraints[:]:
                pose_bone.constraints.remove(constraint)
        _edit_deform_hierarchy(
            context,
            preview,
            source_rig,
            parents,
            bone_names,
            profile.disconnect_bones,
        )
        ensure_preview_constraints(preview, source_rig)
        profile.preview_object = preview
        profile.game_rig = preview
        profile.preview_signature = hierarchy_signature(
            source_rig, profile.hierarchy_mode, profile.disconnect_bones
        )
        profile.preview_valid = True
        profile.configuration_valid = False
        profile.export_bones.clear()
        return preview
    except Exception:
        if preview.name in bpy.data.objects:
            bpy.data.objects.remove(preview, do_unlink=True)
        if armature.users == 0:
            bpy.data.armatures.remove(armature)
        cleanup_empty_preview_collection()
        if profile.source_rig and profile.source_rig.name in bpy.data.objects:
            profile.source_rig.hide_set(profile.preview_source_was_hidden)
        profile.source_rig = None
        profile.game_rig = None
        profile.preview_source_was_hidden = False
        raise


def action_slots_for_rig(action, rig):
    """Find Action slots that are likely to target this rig."""
    if getattr(action, "is_action_legacy", False):
        return [""]

    slots = [slot for slot in action.slots if slot.target_id_type == "OBJECT"]
    if not slots:
        return []

    animation_data = rig.animation_data
    if animation_data and animation_data.action == action:
        current_slot = getattr(animation_data, "action_slot", None)
        if current_slot:
            return [current_slot.identifier]

    expected = "OB" + rig.name
    matching = [
        slot.identifier
        for slot in slots
        if slot.identifier == expected or slot.name_display == rig.name
    ]
    return matching


def add_selected_action_slots(rig, profile, action):
    if not action:
        return 0

    existing = {
        (item.action.as_pointer(), item.action_slot_identifier): item
        for item in profile.action_items
        if item.action
    }
    slot_ids = action_slots_for_rig(action, rig)
    if not slot_ids and not getattr(action, "is_action_legacy", False):
        slot_ids = [
            slot.identifier
            for slot in action.slots
            if slot.target_id_type == "OBJECT"
        ]
        if not slot_ids and profile_has_shape_keys(profile) and any(
            slot.target_id_type == "KEY" for slot in action.slots
        ):
            # A shape-key-only Action has no Rig slot to bake. Keep an empty
            # identifier so export_one_action can distinguish that case.
            slot_ids = [""]
    added = 0
    for slot_identifier in slot_ids:
        key = (action.as_pointer(), slot_identifier)
        existing_item = existing.get(key)
        if existing_item:
            initialize_action_item_frame_range(existing_item)
            if not existing_item.user_added:
                existing_item.user_added = True
                existing_item.enabled = True
                if not existing_item.baked_action_name:
                    existing_item.baked_action_name = "BAKED_" + action.name
                added += 1
            continue
        item = profile.action_items.add()
        item.action = action
        item.action_slot_identifier = slot_identifier
        item.user_added = True
        item.enabled = True
        item.baked_action_name = "BAKED_" + action.name
        initialize_action_item_frame_range(item)
        existing[key] = item
        added += 1
    return added


def initialize_action_item_frame_range(item):
    """Seed the editable Set range from the Action once, like Game Rig Tools."""
    if not item or not item.action or item.set_frame_range_initialized:
        return
    start = int(item.action.frame_range[0])
    end = int(item.action.frame_range[1])
    item.set_frame_start = start
    item.set_frame_end = end
    item.set_frame_range_initialized = True


def action_frame_range(action, action_item=None):
    """Return inclusive integer bake bounds for one Action list item."""
    action_start = int(action.frame_range[0])
    action_end = int(action.frame_range[1])
    mode = getattr(action_item, "frame_range_mode", "ACTION") if action_item else "ACTION"

    if mode == "SET":
        start = int(action_item.set_frame_start)
        end = int(action_item.set_frame_end)
    elif mode == "TRIM":
        start = action_start + int(action_item.trim_start)
        end = action_end - int(action_item.trim_end)
    else:
        start = action_start
        end = action_end

    if start > end:
        raise ValueError(
            f"Invalid frame range for {action.name!r}: start {start} must not be after end {end}."
        )
    return start, end


def baked_action_name(action, action_item=None):
    if action_item and action_item.use_baked_action_name:
        return _safe_name(action_item.baked_action_name or ("BAKED_" + action.name))
    return _safe_name(action.name)


def associated_meshes(scene, rig):
    return [
        obj
        for obj in scene.objects
        if obj.type == "MESH"
        and any(
            modifier.type == "ARMATURE" and modifier.object == rig
            for modifier in obj.modifiers
        )
    ]


def selected_profile_meshes(profile):
    return [
        item.mesh
        for item in getattr(profile, "mesh_items", ())
        if item.enabled and item.mesh and item.mesh.type == "MESH"
    ]


def profile_has_shape_keys(profile):
    return bool(profile_shape_key_data(profile))


def profile_shape_key_data(profile):
    if not profile.include_mesh:
        return []
    key_data_blocks = []
    seen = set()
    for mesh in selected_profile_meshes(profile):
        key_data = getattr(mesh.data, "shape_keys", None)
        if not key_data or key_data.as_pointer() in seen:
            continue
        seen.add(key_data.as_pointer())
        key_data_blocks.append(key_data)
    return key_data_blocks


def action_slots_for_shape_keys(action, profile):
    """Return (Key datablock, slot identifier) pairs for exported profile meshes."""
    if not profile.include_mesh:
        return []

    key_data_blocks = profile_shape_key_data(profile)

    if getattr(action, "is_action_legacy", False):
        assignments = []
        for key_data in key_data_blocks:
            for fcurve in action.fcurves:
                try:
                    key_data.path_resolve(fcurve.data_path, False)
                except (ValueError, TypeError, AttributeError):
                    continue
                assignments.append((key_data, ""))
                break
        return assignments

    key_slots = [slot for slot in action.slots if slot.target_id_type == "KEY"]
    if not key_slots:
        return []

    assignments = []
    for key_data in key_data_blocks:
        animation_data = key_data.animation_data
        current_slot = (
            getattr(animation_data, "action_slot", None)
            if animation_data and animation_data.action == action
            else None
        )
        if current_slot and current_slot in key_slots:
            assignments.append((key_data, current_slot.identifier))
            continue

        used_slots = []
        for slot in key_slots:
            try:
                if key_data in slot.users():
                    used_slots.append(slot)
            except (AttributeError, RuntimeError, TypeError):
                pass
        if len(used_slots) == 1:
            assignments.append((key_data, used_slots[0].identifier))
            continue

        expected = "KE" + key_data.name
        matching = [slot for slot in key_slots if slot.identifier == expected]
        if not matching:
            matching = [
                slot for slot in key_slots if slot.name_display == key_data.name
            ]
        if len(matching) == 1:
            assignments.append((key_data, matching[0].identifier))
        elif len(key_data_blocks) == 1 and len(key_slots) == 1:
            # A lone Key datablock and lone KEY slot are unambiguous even if
            # the slot was renamed after it was created.
            assignments.append((key_data, key_slots[0].identifier))

    return assignments


def _safe_name(value):
    value = re.sub(r"[^A-Za-z0-9_. -]+", "_", value).strip(" ._")
    return value or "RigExport"


def default_pack_file_name(profile_name):
    return _safe_name((profile_name or "RigExport") + "_Animations")


def _add_source_pose_constraints(export_rig, source_rig):
    for pose_bone in export_rig.pose.bones:
        location = pose_bone.constraints.new("COPY_LOCATION")
        location.target = source_rig
        location.subtarget = pose_bone.name
        location.owner_space = "WORLD"
        location.target_space = "WORLD"

        rotation = pose_bone.constraints.new("COPY_ROTATION")
        rotation.target = source_rig
        rotation.subtarget = pose_bone.name
        rotation.owner_space = "WORLD"
        rotation.target_space = "WORLD"


def _create_export_rig(
    context, source_rig, profile, parents, bone_names=None, collection=None
):
    if collection is None:
        collection = _export_temp_collection(context.scene)
    export_name = _safe_name(source_rig.name + "_" + profile.name)
    export_rig = source_rig.copy()
    armature = source_rig.data.copy()
    export_rig.data = armature
    export_rig.name = export_name
    armature.name = export_name + "_Armature"
    _link_export_object(collection, export_rig)
    try:
        source_matrix = source_rig.matrix_world.copy()
        export_rig.parent = None
        for constraint in export_rig.constraints[:]:
            export_rig.constraints.remove(constraint)
        export_rig.matrix_world = source_matrix
        export_rig.animation_data_clear()
        armature.animation_data_clear()
        export_rig.id_properties_clear()
        armature.id_properties_clear()
        export_rig.hide_set(False)
        export_rig.hide_viewport = False
        export_rig.display_type = "SOLID"
        export_rig.show_in_front = True
        for pose_bone in export_rig.pose.bones:
            for constraint in pose_bone.constraints[:]:
                pose_bone.constraints.remove(constraint)
        context.view_layer.update()

        deform_names = set(
            bone_names
            if bone_names is not None
            else [bone.name for bone in source_rig.data.bones if bone.use_deform]
        )
        missing = deform_names.difference(source_rig.data.bones.keys())
        if missing:
            raise RuntimeError(
                "The source Rigify rig is missing export bone(s): "
                + ", ".join(sorted(missing)[:5])
            )
        if not deform_names:
            raise RuntimeError("The export profile has no deform bones selected.")
        _edit_deform_hierarchy(
            context,
            export_rig,
            source_rig,
            parents,
            deform_names,
            profile.disconnect_bones,
        )

        _add_source_pose_constraints(export_rig, source_rig)
        return export_rig, armature
    except Exception:
        if export_rig.mode != "OBJECT":
            context.view_layer.objects.active = export_rig
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.data.objects.remove(export_rig, do_unlink=True)
        if armature.users == 0:
            bpy.data.armatures.remove(armature)
        raise


def _assign_source_action(source_rig, action, slot_identifier):
    had_animation_data = source_rig.animation_data is not None
    animation_data = source_rig.animation_data_create()
    state = {
        "had_animation_data": had_animation_data,
        "action": animation_data.action,
        "slot": getattr(animation_data, "action_slot", None),
        "tracks": [(track, track.mute) for track in animation_data.nla_tracks],
        "use_nla": getattr(animation_data, "use_nla", None),
        "action_blend_type": getattr(animation_data, "action_blend_type", None),
    }

    for track in animation_data.nla_tracks:
        track.mute = True
    if state["use_nla"] is not None:
        animation_data.use_nla = False
    animation_data.action = action
    if hasattr(animation_data, "action_slot"):
        if action:
            selected_slot = next(
                (
                    slot
                    for slot in action.slots
                    if slot.identifier == slot_identifier
                ),
                None,
            ) if slot_identifier else None
            if selected_slot:
                animation_data.action_slot = selected_slot
    if state["action_blend_type"] is not None:
        animation_data.action_blend_type = "REPLACE"
    return state


def _restore_source_action(source_rig, state):
    if not state:
        return
    animation_data = source_rig.animation_data
    if not animation_data:
        return
    if not state["had_animation_data"]:
        source_rig.animation_data_clear()
        return
    animation_data.action = state["action"]
    if hasattr(animation_data, "action_slot") and state["action"] and state["slot"]:
        animation_data.action_slot = state["slot"]
    for track, muted in state["tracks"]:
        track.mute = muted
    if state["use_nla"] is not None:
        animation_data.use_nla = state["use_nla"]
    if state["action_blend_type"] is not None:
        animation_data.action_blend_type = state["action_blend_type"]


def _bake_action(context, source_rig, export_rig, source_action, slot_identifier, frame_bounds):
    source_state = _assign_source_action(source_rig, source_action, slot_identifier)
    frame_start, frame_end = frame_bounds

    try:
        # Match Game Rig Tools' Action Bakery call shape. It bakes an object
        # list with no destination Action, then takes the first returned Action.
        baked_actions = anim_utils.bake_action_objects(
            [[export_rig, None]],
            frames=range(frame_start, frame_end + 1),
            bake_options=anim_utils.BakeOptions(
                only_selected=False,
                do_pose=True,
                do_object=False,
                do_visual_keying=True,
                do_constraint_clear=False,
                do_parents_clear=False,
                do_clean=False,
                do_location=True,
                do_rotation=True,
                do_scale=True,
                do_bbone=True,
                do_custom_props=True,
            ),
        )
        baked_action = baked_actions[0] if baked_actions else None
        if baked_action is None:
            raise RuntimeError("Game Rig Tools' visual pose bake did not return an Action.")
        for pose_bone in export_rig.pose.bones:
            for constraint in pose_bone.constraints[:]:
                pose_bone.constraints.remove(constraint)
        context.view_layer.update()
        return baked_action
    finally:
        _restore_source_action(source_rig, source_state)


def _copy_rig_meshes(context, source_rig, export_rig, profile, collection):
    copies = []
    name_pairs = []
    export_bone_names = set(export_rig.data.bones.keys())
    for mesh in selected_profile_meshes(profile):
        mesh_copy = mesh.copy()
        mesh_copy.name = mesh.name
        _link_export_object(collection, mesh_copy)
        mesh_copy.matrix_world = mesh.matrix_world.copy()
        mesh_copy.parent = export_rig
        mesh_copy.matrix_world = mesh.matrix_world.copy()
        mesh_group_names = {group.name for group in mesh_copy.vertex_groups}
        for modifier in mesh_copy.modifiers:
            if modifier.type != "ARMATURE":
                continue
            bound_rig = modifier.object
            if bound_rig == source_rig:
                modifier.object = export_rig
                continue
            if not bound_rig or bound_rig.type != "ARMATURE" or not bound_rig.data:
                continue
            bound_bone_names = set(bound_rig.data.bones.keys())
            if (
                export_bone_names.issubset(bound_bone_names)
                and mesh_group_names.intersection(export_bone_names)
            ):
                # Some characters skin meshes to a separate deform rig whose
                # bone names match the export rig, rather than the control rig.
                modifier.object = export_rig
        mesh_copy.hide_set(False)
        mesh_copy.hide_viewport = False
        copies.append(mesh_copy)
        name_pairs.append((mesh, mesh_copy, mesh.name))
    return copies, name_pairs


def _suspend_armature_mesh_modifiers(meshes):
    """Prevent Better FBX's evaluated modifier pass from baking the current pose."""
    states = []
    for mesh in meshes:
        for modifier in mesh.modifiers:
            if modifier.type != "ARMATURE" or not modifier.object:
                continue
            states.append((modifier, modifier.show_viewport, modifier.show_render))
            modifier.show_viewport = False
            modifier.show_render = False
    if states:
        bpy.context.view_layer.update()
    return states


def _restore_armature_mesh_modifiers(states):
    for modifier, show_viewport, show_render in states:
        modifier.show_viewport = show_viewport
        modifier.show_render = show_render
    if states:
        bpy.context.view_layer.update()


def _prepare_export_mesh_names(name_pairs):
    """Give export copies the source names while freeing those names."""
    for source_mesh, _mesh_copy, original_name in name_pairs:
        source_mesh.name = "REX_EXPORT_SOURCE_" + original_name
    for _source_mesh, mesh_copy, original_name in name_pairs:
        mesh_copy.name = original_name


def _restore_export_mesh_names(name_pairs, export_succeeded):
    for source_mesh, mesh_copy, original_name in name_pairs:
        if (
            not export_succeeded
            and bpy.data.objects.get(mesh_copy.name) == mesh_copy
        ):
            mesh_copy.name = original_name + "_REX_EXPORT_FAILED"
    for source_mesh, _mesh_copy, original_name in name_pairs:
        if bpy.data.objects.get(source_mesh.name) == source_mesh:
            source_mesh.name = original_name


def _remove_temporary(objects, data_blocks=(), actions=()):
    for obj in objects:
        if obj and bpy.data.objects.get(obj.name) == obj:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data and data.users == 0:
                if isinstance(data, bpy.types.Armature):
                    bpy.data.armatures.remove(data)
    for action in actions:
        if action and action.users == 0 and action.name in bpy.data.actions:
            bpy.data.actions.remove(action)


def _temporarily_free_action_name(name):
    """Free an Action datablock name so its export bake can use it exactly."""
    conflict = bpy.data.actions.get(name)
    if not conflict:
        return None

    original_name = conflict.name
    temporary_base = "REX_EXPORT_SOURCE_" + original_name
    temporary_name = temporary_base
    suffix = 1
    while bpy.data.actions.get(temporary_name) not in (None, conflict):
        temporary_name = f"{temporary_base}_{suffix}"
        suffix += 1
    conflict.name = temporary_name
    return conflict, original_name


def _restore_action_name_conflict(conflict_state, temporary_actions, export_succeeded):
    if not conflict_state:
        return
    conflict, original_name = conflict_state
    if bpy.data.actions.get(conflict.name) != conflict:
        return

    for action in temporary_actions:
        if (
            action
            and action != conflict
            and bpy.data.actions.get(action.name) == action
            and action.name == original_name
        ):
            suffix = "_REX_EXPORT_TEMP" if export_succeeded else "_REX_EXPORT_FAILED"
            action.name = original_name + suffix
    conflict.name = original_name


def export_static_fbx(context, source_rig, profile):
    """Export the configured rig and optional meshes without animation."""
    if not getattr(profile, "configuration_valid", False):
        raise RuntimeError("Generate a Game Rig and add the profile to the export UI before exporting.")
    current_signature = hierarchy_signature(
        source_rig, profile.hierarchy_mode, profile.disconnect_bones
    )
    if profile.source_signature != current_signature:
        raise RuntimeError("The source Rigify rig changed. ReGen the Game Rig and add the profile to the UI again.")
    bone_names, parents = export_bone_map(profile)
    if not bone_names:
        raise RuntimeError("The export profile has no deform bones selected.")
    if profile.include_mesh and not selected_profile_meshes(profile):
        raise RuntimeError("Include Mesh is enabled, but no mesh is enabled in the profile's mesh list.")

    output_directory = bpy.path.abspath(profile.output_directory or "//")
    os.makedirs(output_directory, exist_ok=True)
    filepath = os.path.join(output_directory, _safe_name(profile.name) + ".fbx")

    previous_active = context.view_layer.objects.active
    previous_selected = list(context.selected_objects)
    previous_mode = source_rig.mode
    previous_frame = context.scene.frame_current
    original_rig_matrix = source_rig.matrix_world.copy()
    temporary_objects = []
    mesh_name_pairs = []
    export_collection = _export_temp_collection(context.scene)
    export_succeeded = False
    try:
        if source_rig.mode != "OBJECT":
            context.view_layer.objects.active = source_rig
            bpy.ops.object.mode_set(mode="OBJECT")

        export_rig, _armature_data = _create_export_rig(
            context,
            source_rig,
            profile,
            parents,
            bone_names=bone_names,
            collection=export_collection,
        )
        temporary_objects.append(export_rig)
        mesh_copies, mesh_name_pairs = (
            _copy_rig_meshes(
                context, source_rig, export_rig, profile, export_collection
            )
            if profile.include_mesh
            else ([], [])
        )
        temporary_objects.extend(mesh_copies)

        bpy.ops.object.select_all(action="DESELECT")
        export_rig.select_set(True)
        for mesh_copy in mesh_copies:
            mesh_copy.select_set(True)
        context.view_layer.objects.active = export_rig
        context.view_layer.update()
        _prepare_export_mesh_names(mesh_name_pairs)

        armature_modifier_states = _suspend_armature_mesh_modifiers(mesh_copies)
        try:
            result = _better_fbx_export(filepath, profile, use_animation=False)
        finally:
            _restore_armature_mesh_modifiers(armature_modifier_states)
        if "FINISHED" not in result or not os.path.isfile(filepath):
            raise RuntimeError("Better FBX did not create the output file.")
        export_succeeded = True
        return filepath
    finally:
        if export_succeeded:
            _remove_temporary(temporary_objects)
        _cleanup_empty_export_collection()
        _restore_export_mesh_names(mesh_name_pairs, export_succeeded)
        source_rig.matrix_world = original_rig_matrix
        context.scene.frame_set(previous_frame)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in previous_selected:
            if bpy.data.objects.get(obj.name) == obj:
                obj.select_set(True)
        if previous_active and bpy.data.objects.get(previous_active.name) == previous_active:
            context.view_layer.objects.active = previous_active
            if previous_mode in {"POSE", "EDIT"} and previous_active.mode != previous_mode:
                bpy.ops.object.mode_set(mode=previous_mode)


def export_one_action(
    context, source_rig, profile, action, slot_identifier, action_item=None
):
    if not getattr(profile, "configuration_valid", False):
        raise RuntimeError("Generate a Game Rig and add the profile to the export UI before exporting.")
    current_signature = hierarchy_signature(
        source_rig, profile.hierarchy_mode, profile.disconnect_bones
    )
    if profile.source_signature != current_signature:
        raise RuntimeError("The source Rigify rig changed. ReGen the Game Rig and add the profile to the UI again.")
    bone_names, parents = export_bone_map(profile)
    if not bone_names:
        raise RuntimeError("The export profile has no deform bones selected.")

    output_directory = bpy.path.abspath(profile.output_directory or "//")
    os.makedirs(output_directory, exist_ok=True)
    action_name = baked_action_name(action, action_item)
    filename = "{}_{}.fbx".format(_safe_name(profile.name), action_name)
    filepath = os.path.join(output_directory, filename)
    frame_bounds = action_frame_range(action, action_item)

    previous_active = context.view_layer.objects.active
    previous_selected = list(context.selected_objects)
    previous_mode = source_rig.mode
    previous_frame = context.scene.frame_current
    previous_scene_name = context.scene.name
    previous_frame_range = (
        context.scene.frame_start,
        context.scene.frame_end,
    )
    original_rig_matrix = source_rig.matrix_world.copy()
    temporary_objects = []
    temporary_actions = []
    temporary_key_states = []
    mesh_name_pairs = []
    export_collection = _export_temp_collection(context.scene)
    action_name_conflict = _temporarily_free_action_name(action_name)
    export_succeeded = False
    try:
        if source_rig.mode != "OBJECT":
            context.view_layer.objects.active = source_rig
            bpy.ops.object.mode_set(mode="OBJECT")

        key_slot_by_data = {
            key_data.as_pointer(): key_slot_identifier
            for key_data, key_slot_identifier in action_slots_for_shape_keys(
                action, profile
            )
        }
        for key_data in profile_shape_key_data(profile):
            key_slot_identifier = key_slot_by_data.get(key_data.as_pointer())
            if key_slot_identifier is None and key_data.animation_data is None:
                continue
            key_state = _assign_source_action(
                key_data,
                action if key_slot_identifier is not None else None,
                key_slot_identifier or "",
            )
            temporary_key_states.append((key_data, key_state))

        export_rig, armature_data = _create_export_rig(
            context,
            source_rig,
            profile,
            parents,
            bone_names=bone_names,
            collection=export_collection,
        )
        temporary_objects.append(export_rig)

        has_rig_slot = getattr(action, "is_action_legacy", False) or any(
            slot.identifier == slot_identifier
            and slot.target_id_type == "OBJECT"
            for slot in getattr(action, "slots", ())
        )
        if has_rig_slot:
            baked_action = _bake_action(
                context, source_rig, export_rig, action, slot_identifier, frame_bounds
            )
            temporary_actions.append(baked_action)
            baked_action.name = action_name
        mesh_copies, mesh_name_pairs = (
            _copy_rig_meshes(
                context, source_rig, export_rig, profile, export_collection
            )
            if profile.include_mesh
            else ([], [])
        )
        temporary_objects.extend(mesh_copies)

        bpy.ops.object.select_all(action="DESELECT")
        export_rig.select_set(True)
        for mesh_copy in mesh_copies:
            mesh_copy.select_set(True)
        context.view_layer.objects.active = export_rig

        # Keep the scene range on this clip for Better FBX presets that use
        # the timeline range when sampling animation.
        context.scene.frame_start, context.scene.frame_end = frame_bounds
        _prepare_export_mesh_names(mesh_name_pairs)
        context.scene.name = action_name

        armature_modifier_states = _suspend_armature_mesh_modifiers(mesh_copies)
        try:
            result = _better_fbx_export(
                filepath,
                profile,
                use_animation=True,
                animation_type_override="Active",
            )
        finally:
            _restore_armature_mesh_modifiers(armature_modifier_states)
        if "FINISHED" not in result or not os.path.isfile(filepath):
            raise RuntimeError("Better FBX did not create the output file.")
        export_succeeded = True
        return filepath
    finally:
        for key_data, key_state in reversed(temporary_key_states):
            _restore_source_action(key_data, key_state)
        if export_succeeded:
            _remove_temporary(temporary_objects, actions=temporary_actions)
        _restore_action_name_conflict(
            action_name_conflict, temporary_actions, export_succeeded
        )
        _cleanup_empty_export_collection()
        _restore_export_mesh_names(mesh_name_pairs, export_succeeded)
        context.scene.name = previous_scene_name
        source_rig.matrix_world = original_rig_matrix
        context.scene.frame_start, context.scene.frame_end = previous_frame_range
        context.scene.frame_set(previous_frame)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in previous_selected:
            if bpy.data.objects.get(obj.name) == obj:
                obj.select_set(True)
        if previous_active and bpy.data.objects.get(previous_active.name) == previous_active:
            context.view_layer.objects.active = previous_active
            if previous_mode in {"POSE", "EDIT"} and previous_active.mode != previous_mode:
                bpy.ops.object.mode_set(mode=previous_mode)


def export_actions_packed_nla(context, source_rig, profile, action_items):
    """Export enabled Actions as separate NLA tracks in one Better FBX file."""
    if not getattr(profile, "configuration_valid", False):
        raise RuntimeError(
            "Generate a Game Rig and add the profile to the export UI before exporting."
        )
    if not better_fbx_available():
        raise RuntimeError(
            "Better FBX is not enabled. Enable the Better FBX add-on and try again."
        )
    if profile.include_mesh and not selected_profile_meshes(profile):
        raise RuntimeError(
            "Include Mesh is enabled, but no mesh is enabled in the profile's mesh list."
        )

    items = [
        item
        for item in action_items
        if item and item.action and getattr(item, "enabled", True)
    ]
    if not items:
        raise RuntimeError("No enabled Actions were selected for NLA packing.")

    current_signature = hierarchy_signature(
        source_rig, profile.hierarchy_mode, profile.disconnect_bones
    )
    if profile.source_signature != current_signature:
        raise RuntimeError(
            "The source Rigify rig changed. ReGen the Game Rig and add the profile to the UI again."
        )
    bone_names, parents = export_bone_map(profile)
    if not bone_names:
        raise RuntimeError("The export profile has no deform bones selected.")

    output_directory = bpy.path.abspath(profile.output_directory or "//")
    os.makedirs(output_directory, exist_ok=True)
    output_name = getattr(profile, "pack_file_name", "").strip()
    if not output_name:
        output_name = default_pack_file_name(profile.name)
    output_name = os.path.splitext(output_name)[0]
    filepath = os.path.join(output_directory, _safe_name(output_name) + ".fbx")

    used_track_names = set()
    item_data = []
    for item in items:
        frame_bounds = action_frame_range(item.action, item)
        track_name = _unique_nla_track_name(
            baked_action_name(item.action, item), used_track_names
        )
        item_data.append(
            {
                "item": item,
                "action": item.action,
                "track_name": track_name,
                "frame_bounds": frame_bounds,
                "shape_key_slots": action_slots_for_shape_keys(
                    item.action, profile
                ),
            }
        )

    previous_active = context.view_layer.objects.active
    previous_selected = list(context.selected_objects)
    previous_mode = source_rig.mode
    previous_frame = context.scene.frame_current
    previous_scene_name = context.scene.name
    previous_frame_range = (
        context.scene.frame_start,
        context.scene.frame_end,
    )
    original_rig_matrix = source_rig.matrix_world.copy()
    temporary_objects = []
    temporary_actions = []
    mesh_name_pairs = []
    export_collection = _export_temp_collection(context.scene)
    export_succeeded = False
    try:
        if source_rig.mode != "OBJECT":
            context.view_layer.objects.active = source_rig
            bpy.ops.object.mode_set(mode="OBJECT")

        export_rig, _armature_data = _create_export_rig(
            context,
            source_rig,
            profile,
            parents,
            bone_names=bone_names,
            collection=export_collection,
        )
        temporary_objects.append(export_rig)
        constraints_ready = True
        baked_tracks = []
        shape_key_tracks = []

        for data in item_data:
            action = data["action"]
            item = data["item"]
            slot_identifier = item.action_slot_identifier
            frame_bounds = data["frame_bounds"]
            track_name = data["track_name"]
            has_rig_slot = getattr(action, "is_action_legacy", False) or any(
                slot.identifier == slot_identifier
                and slot.target_id_type == "OBJECT"
                for slot in getattr(action, "slots", ())
            )

            if has_rig_slot:
                if export_rig.animation_data:
                    export_rig.animation_data_clear()
                if not constraints_ready:
                    _add_source_pose_constraints(export_rig, source_rig)
                    context.view_layer.update()
                    constraints_ready = True
                baked_action = _bake_action(
                    context,
                    source_rig,
                    export_rig,
                    action,
                    slot_identifier,
                    frame_bounds,
                )
                temporary_actions.append(baked_action)
                baked_action.name = track_name
                action_slot = getattr(export_rig.animation_data, "action_slot", None)
                baked_tracks.append(
                    (baked_action, track_name, frame_bounds[0], action_slot)
                )
                constraints_ready = False

            if data["shape_key_slots"]:
                for key_data, key_slot_identifier in data["shape_key_slots"]:
                    shape_key_tracks.append(
                        (
                            key_data.as_pointer(),
                            action,
                            track_name,
                            frame_bounds,
                            key_slot_identifier,
                        )
                    )

            if not has_rig_slot and not data["shape_key_slots"]:
                raise RuntimeError(
                    f"Action {action.name!r} has no slot for this rig or its exported mesh shape keys."
                )

        for baked_action, track_name, frame_start, action_slot in baked_tracks:
            _set_action_as_nla_track(
                export_rig,
                baked_action,
                track_name,
                frame_start,
                action_slot=action_slot,
            )

        mesh_copies, mesh_name_pairs = (
            _copy_rig_meshes(
                context, source_rig, export_rig, profile, export_collection
            )
            if profile.include_mesh
            else ([], [])
        )
        temporary_objects.extend(mesh_copies)

        shape_key_targets = {}
        for source_mesh, mesh_copy, _original_name in mesh_name_pairs:
            source_key_data = getattr(source_mesh.data, "shape_keys", None)
            if not source_key_data:
                continue
            mesh_copy.data = source_mesh.data.copy()
            export_key_data = getattr(mesh_copy.data, "shape_keys", None)
            if not export_key_data:
                continue
            if export_key_data.animation_data:
                export_key_data.animation_data_clear()
            shape_key_targets.setdefault(source_key_data.as_pointer(), []).append(
                export_key_data
            )

        for (
            source_key_pointer,
            action,
            track_name,
            frame_bounds,
            key_slot_identifier,
        ) in shape_key_tracks:
            slot = next(
                (
                    candidate
                    for candidate in getattr(action, "slots", ())
                    if candidate.identifier == key_slot_identifier
                ),
                None,
            )
            for export_key_data in shape_key_targets.get(source_key_pointer, ()):
                _set_shape_key_as_nla_track(
                    export_key_data,
                    action,
                    track_name,
                    frame_bounds,
                    action_slot=slot,
                )

        if not baked_tracks and not shape_key_tracks:
            raise RuntimeError("No animation tracks were prepared for this FBX.")

        bpy.ops.object.select_all(action="DESELECT")
        export_rig.select_set(True)
        for mesh_copy in mesh_copies:
            mesh_copy.select_set(True)
        context.view_layer.objects.active = export_rig

        all_starts = [data["frame_bounds"][0] for data in item_data]
        all_ends = [data["frame_bounds"][1] for data in item_data]
        context.scene.frame_start = min(all_starts)
        context.scene.frame_end = max(all_ends)
        _prepare_export_mesh_names(mesh_name_pairs)
        context.scene.name = _safe_name(profile.name + "_Animations")

        armature_modifier_states = _suspend_armature_mesh_modifiers(mesh_copies)
        try:
            result = _better_fbx_export(
                filepath,
                profile,
                use_animation=True,
                animation_type_override="Tracks",
            )
        finally:
            _restore_armature_mesh_modifiers(armature_modifier_states)
        if "FINISHED" not in result or not os.path.isfile(filepath):
            raise RuntimeError("Better FBX did not create the packed output file.")
        export_succeeded = True
        return filepath
    finally:
        if export_succeeded:
            _remove_temporary(temporary_objects, actions=temporary_actions)
        _cleanup_empty_export_collection()
        _restore_export_mesh_names(mesh_name_pairs, export_succeeded)
        context.scene.name = previous_scene_name
        source_rig.matrix_world = original_rig_matrix
        context.scene.frame_start, context.scene.frame_end = previous_frame_range
        context.scene.frame_set(previous_frame)
        bpy.ops.object.select_all(action="DESELECT")
        for obj in previous_selected:
            if bpy.data.objects.get(obj.name) == obj:
                obj.select_set(True)
        if previous_active and bpy.data.objects.get(previous_active.name) == previous_active:
            context.view_layer.objects.active = previous_active
            if previous_mode in {"POSE", "EDIT"} and previous_active.mode != previous_mode:
                bpy.ops.object.mode_set(mode=previous_mode)
