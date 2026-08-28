"""Rigify Action Euler-to-Quaternion conversion tool.

This feature is intentionally scoped to the generated rig named
``RIG-Meta_Armature_2``. It can create a new Action with the ``_QUAT`` suffix
or overwrite the source Action when that option is enabled. Only the union
of the source Euler keyframe times is sampled; it does not bake every frame.
"""

import json
from collections import defaultdict

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, PropertyGroup, UIList, UI_UL_list
from mathutils import Euler


RIG_NAME = "RIG-Meta_Armature_2"
METARIG_NAME = "Meta_Armature_2"
ACTION_SUFFIX = "_QUAT"
STATE_KEY = "rigify_quaternion_action_converter_state"
DEFAULT_EULER_ORDER = "XYZ"
EULER_ORDERS = {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}
CLAVICLE_NAMES = {
    "Bip001 Clavicle.L",
    "Bip001 Clavicle.R",
}
INTERNAL_PREFIXES = (
    "DEF-",
    "ORG-",
    "MCH-",
    "VIS_",
)
NLA_STRIP_PROPERTIES = (
    "frame_start",
    "frame_end",
    "action_frame_start",
    "action_frame_end",
    "scale",
    "repeat",
    "blend_type",
    "extrapolation",
    "influence",
    "strip_time",
    "use_auto_blend",
    "use_animated_influence",
    "use_animated_time",
    "use_animated_time_cyclic",
    "use_reverse",
    "use_sync_length",
)
NLA_TRACK_PROPERTIES = (
    "mute",
    "is_solo",
    "select",
    "lock",
)
ANIMATION_DATA_PROPERTIES = (
    "use_nla",
    "use_pin",
    "use_tweak_mode",
)


def _unique_action_name(base_name):
    if bpy.data.actions.get(base_name) is None:
        return base_name
    index = 1
    while bpy.data.actions.get(f"{base_name}.{index:03d}") is not None:
        index += 1
    return f"{base_name}.{index:03d}"


def _target_rig():
    rig = bpy.data.objects.get(RIG_NAME)
    if rig is None or rig.type != "ARMATURE":
        return None
    return rig


def _metarig():
    metarig = bpy.data.objects.get(METARIG_NAME)
    if metarig is None or metarig.type != "ARMATURE":
        return None
    return metarig


def _live_object(obj):
    try:
        return obj if obj and obj.name in bpy.data.objects else None
    except ReferenceError:
        return None


def _capture_context(context):
    return {
        "selected_objects": tuple(context.selected_objects),
        "active_object": context.view_layer.objects.active,
        "mode": context.object.mode if context.object else "OBJECT",
        "frame": context.scene.frame_current,
    }


def _restore_context(context, state):
    if context.object and context.object.mode != "OBJECT":
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in state["selected_objects"]:
        live_obj = _live_object(obj)
        if live_obj:
            live_obj.select_set(True)

    active_object = _live_object(state["active_object"])
    if active_object:
        context.view_layer.objects.active = active_object
        mode = state["mode"]
        if mode != "OBJECT" and bpy.ops.object.mode_set.poll():
            try:
                bpy.ops.object.mode_set(mode=mode)
            except RuntimeError:
                pass
    try:
        context.scene.frame_set(state["frame"])
    except (AttributeError, RuntimeError):
        pass


def _safe_get(owner, property_name, default=None):
    try:
        return getattr(owner, property_name)
    except (AttributeError, ReferenceError, RuntimeError):
        return default


def _try_set(owner, property_name, value):
    try:
        setattr(owner, property_name, value)
        return True
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
        return False


def _slot_state(owner):
    slot = _safe_get(owner, "action_slot")
    return {
        "identifier": _safe_get(slot, "identifier"),
        "handle": _safe_get(owner, "action_slot_handle"),
    }


def _capture_nla_strip(strip):
    state = {
        "name": strip.name,
        "action": strip.action,
        "slot": _slot_state(strip),
        "properties": {},
        "fcurves": [],
    }
    for property_name in NLA_STRIP_PROPERTIES:
        value = _safe_get(strip, property_name)
        if value is not None:
            state["properties"][property_name] = value

    for fcurve in _safe_get(strip, "fcurves", ()) or ():
        fcurve_state = {
            "data_path": fcurve.data_path,
            "array_index": int(fcurve.array_index),
            "extrapolation": _safe_get(fcurve, "extrapolation"),
            "keyframes": [],
        }
        for point in fcurve.keyframe_points:
            point_state = {
                "frame": float(point.co[0]),
                "value": float(point.co[1]),
                "interpolation": _safe_get(point, "interpolation"),
                "easing": _safe_get(point, "easing"),
                "handle_left_type": _safe_get(point, "handle_left_type"),
                "handle_right_type": _safe_get(point, "handle_right_type"),
            }
            left = _safe_get(point, "handle_left")
            right = _safe_get(point, "handle_right")
            if left is not None:
                point_state["handle_left"] = tuple(left)
            if right is not None:
                point_state["handle_right"] = tuple(right)
            fcurve_state["keyframes"].append(point_state)
        state["fcurves"].append(fcurve_state)
    return state


def _capture_animation_data(obj):
    """Capture animation assignments that Rigify Generate clears."""
    animation_data = obj.animation_data if obj else None
    if animation_data is None:
        return None

    active_track = _safe_get(animation_data.nla_tracks, "active")
    state = {
        "action": animation_data.action,
        "action_slot": _slot_state(animation_data),
        "properties": {
            property_name: _safe_get(animation_data, property_name)
            for property_name in ANIMATION_DATA_PROPERTIES
        },
        "active_track_name": _safe_get(active_track, "name"),
        "nla_tracks": [],
    }
    for track in animation_data.nla_tracks:
        track_state = {
            "name": track.name,
            "properties": {
                property_name: _safe_get(track, property_name)
                for property_name in NLA_TRACK_PROPERTIES
            },
            "strips": [_capture_nla_strip(strip) for strip in track.strips],
        }
        state["nla_tracks"].append(track_state)
    return state


def _set_action_slot(owner, action, object_name, slot_identifier=None, slot_handle=None):
    slots = _safe_get(action, "slots")
    if slots is None or len(slots) == 0:
        return False

    chosen = None
    if slot_identifier:
        chosen = next(
            (
                slot
                for slot in slots
                if _safe_get(slot, "identifier") == slot_identifier
            ),
            None,
        )
    if chosen is None and slot_handle is not None:
        chosen = next(
            (
                slot
                for slot in slots
                if _safe_get(slot, "handle") == slot_handle
            ),
            None,
        )
    if chosen is None:
        expected_identifier = f"OB{object_name}"
        chosen = next(
            (
                slot
                for slot in slots
                if _safe_get(slot, "identifier") == expected_identifier
            ),
            None,
        )
    chosen = chosen or slots[0]

    if _try_set(owner, "action_slot", chosen):
        return True
    handle = _safe_get(chosen, "handle")
    return handle is not None and _try_set(owner, "action_slot_handle", handle)


def _restore_nla_strip_fcurves(strip, fcurve_states):
    for fcurve_state in fcurve_states:
        try:
            fcurve = strip.fcurves.new(
                data_path=fcurve_state["data_path"],
                index=fcurve_state["array_index"],
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
        extrapolation = fcurve_state.get("extrapolation")
        if extrapolation is not None:
            _try_set(fcurve, "extrapolation", extrapolation)
        for point_state in fcurve_state["keyframes"]:
            try:
                point = fcurve.keyframe_points.insert(
                    point_state["frame"],
                    point_state["value"],
                    options={"FAST"},
                )
            except (RuntimeError, TypeError, ValueError):
                continue
            for property_name in (
                "interpolation",
                "easing",
                "handle_left_type",
                "handle_right_type",
            ):
                value = point_state.get(property_name)
                if value is not None:
                    _try_set(point, property_name, value)
            for property_name in ("handle_left", "handle_right"):
                value = point_state.get(property_name)
                if value is not None:
                    _try_set(point, property_name, value)
        try:
            fcurve.update()
        except (AttributeError, RuntimeError):
            pass


def _restore_animation_data(obj, state):
    """Restore Active Action and NLA data after Rigify regenerates the rig."""
    result = {
        "active_action": False,
        "nla_strips": 0,
        "missing_actions": 0,
    }
    if obj is None or state is None:
        return result

    animation_data = obj.animation_data_create()
    for track in list(animation_data.nla_tracks):
        animation_data.nla_tracks.remove(track)

    action = state.get("action")
    live_action = bpy.data.actions.get(action.name) if action else None
    if live_action is not None:
        slot = state.get("action_slot", {})
        _restore_action_slot(
            animation_data,
            live_action,
            obj.name,
            slot_identifier=slot.get("identifier"),
            slot_handle=slot.get("handle"),
        )
        result["active_action"] = True
    else:
        _try_set(animation_data, "action", None)

    for track_state in state.get("nla_tracks", ()):
        track = animation_data.nla_tracks.new()
        _try_set(track, "name", track_state.get("name", "NLA Track"))

        for strip_state in track_state.get("strips", ()):
            source_action = strip_state.get("action")
            live_action = (
                bpy.data.actions.get(source_action.name)
                if source_action
                else None
            )
            if live_action is None:
                result["missing_actions"] += 1
                continue

            properties = strip_state.get("properties", {})
            strip = track.strips.new(
                strip_state.get("name", live_action.name),
                0,
                live_action,
            )

            # Disable sync while restoring exact frame boundaries, then put it
            # back to the source value after all timing properties are copied.
            if "use_sync_length" in properties:
                _try_set(strip, "use_sync_length", False)
            for property_name in (
                "action_frame_start",
                "action_frame_end",
                "frame_start",
                "frame_end",
                "scale",
                "repeat",
                "blend_type",
                "extrapolation",
                "influence",
                "strip_time",
                "use_auto_blend",
                "use_animated_influence",
                "use_animated_time",
                "use_animated_time_cyclic",
                "use_reverse",
            ):
                if property_name in properties:
                    _try_set(strip, property_name, properties[property_name])
            if "use_sync_length" in properties:
                _try_set(strip, "use_sync_length", properties["use_sync_length"])

            slot = strip_state.get("slot", {})
            _set_action_slot(
                strip,
                live_action,
                obj.name,
                slot_identifier=slot.get("identifier"),
                slot_handle=slot.get("handle"),
            )
            _restore_nla_strip_fcurves(strip, strip_state.get("fcurves", ()))
            result["nla_strips"] += 1

        for property_name in NLA_TRACK_PROPERTIES:
            value = track_state.get("properties", {}).get(property_name)
            if value is not None:
                _try_set(track, property_name, value)

    active_track_name = state.get("active_track_name")
    if active_track_name:
        active_track = animation_data.nla_tracks.get(active_track_name)
        if active_track is not None:
            _try_set(animation_data.nla_tracks, "active", active_track)
    for property_name in ANIMATION_DATA_PROPERTIES:
        value = state.get("properties", {}).get(property_name)
        if value is not None:
            _try_set(animation_data, property_name, value)
    return result


def _set_metarig_rotation_modes(metarig):
    quaternion_count = 0
    clavicle_count = 0
    for pose_bone in metarig.pose.bones:
        if pose_bone.name in CLAVICLE_NAMES:
            pose_bone.rotation_mode = "XYZ"
            clavicle_count += 1
        else:
            pose_bone.rotation_mode = "QUATERNION"
            quaternion_count += 1
    return quaternion_count, clavicle_count


def _active_action(rig):
    animation_data = rig.animation_data if rig else None
    return animation_data.action if animation_data else None


def _is_user_control_bone(name):
    return not name.startswith(INTERNAL_PREFIXES)


def _parse_pose_rotation_path(data_path):
    prefix = 'pose.bones["'
    if prefix not in data_path:
        return None
    rest = data_path.split(prefix, 1)[1]
    end = rest.find('"]')
    if end < 0:
        return None
    bone_name = rest[:end]
    suffix = rest[end + 2 :]
    if suffix.startswith("."):
        suffix = suffix[1:]
    if suffix.startswith("rotation_euler"):
        return bone_name, "rotation_euler"
    if suffix.startswith("rotation_quaternion"):
        return bone_name, "rotation_quaternion"
    if suffix.startswith("rotation_axis_angle"):
        return bone_name, "rotation_axis_angle"
    return None


def _iter_action_fcurves(action):
    """Yield (channelbag, fcurve) for Blender 5.x and legacy Actions."""
    legacy_fcurves = getattr(action, "fcurves", None)
    if legacy_fcurves is not None:
        for fcurve in legacy_fcurves:
            yield None, fcurve
        return

    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for channelbag in strip.channelbags:
                for fcurve in channelbag.fcurves:
                    yield channelbag, fcurve


def _action_rotation_curves(action):
    euler = defaultdict(list)
    quaternion = defaultdict(list)
    axis_angle = defaultdict(list)
    for channelbag, fcurve in _iter_action_fcurves(action):
        parsed = _parse_pose_rotation_path(fcurve.data_path)
        if parsed is None:
            continue
        bone_name, rotation_property = parsed
        if rotation_property == "rotation_euler":
            euler[bone_name].append((channelbag, fcurve))
        elif rotation_property == "rotation_quaternion":
            quaternion[bone_name].append((channelbag, fcurve))
        elif rotation_property == "rotation_axis_angle":
            axis_angle[bone_name].append((channelbag, fcurve))
    return euler, quaternion, axis_angle


def _keyframe_frames(curves):
    frames = set()
    for _channelbag, fcurve in curves:
        for point in fcurve.keyframe_points:
            frames.add(round(float(point.co[0]), 6))
    return sorted(frames)


def _curve_by_index(curves):
    return {int(fcurve.array_index): fcurve for _channelbag, fcurve in curves}


def _convertible_bones(action, rig):
    euler_curves, _quaternion_curves, _axis_angle_curves = _action_rotation_curves(action)
    return {
        bone_name
        for bone_name in euler_curves
        if bone_name in rig.pose.bones
        and _is_user_control_bone(bone_name)
        and bone_name not in CLAVICLE_NAMES
    }


def _rotation_key_count(action, rig):
    euler_curves, _quaternion_curves, _axis_angle_curves = _action_rotation_curves(action)
    return sum(
        len(_keyframe_frames(euler_curves[bone_name]))
        for bone_name in euler_curves
        if bone_name in rig.pose.bones
        and _is_user_control_bone(bone_name)
        and bone_name not in CLAVICLE_NAMES
    )


def _load_saved_rotation_modes(scene):
    raw_state = scene.get(STATE_KEY)
    if not raw_state:
        return {}
    try:
        state = json.loads(raw_state) if isinstance(raw_state, str) else raw_state
    except (TypeError, ValueError):
        return {}
    modes = state.get("source_rotation_modes", {}) if isinstance(state, dict) else {}
    return {name: mode for name, mode in modes.items() if mode in EULER_ORDERS}


def _resolve_rotation_modes(scene, rig, actions):
    """Resolve source Euler orders without requiring an *_OLD rig.

    The previous converter stores the original orders on the Scene. When
    that state is unavailable, the rig setup used for this tool starts from
    Blender's default XYZ order, so XYZ is used as a documented fallback.
    """
    modes = _load_saved_rotation_modes(scene)
    fallback_names = set()
    candidate_names = set()
    for action in actions:
        candidate_names.update(_convertible_bones(action, rig))

    for bone_name in sorted(candidate_names):
        if bone_name in modes:
            continue
        current_mode = rig.pose.bones[bone_name].rotation_mode
        if current_mode in EULER_ORDERS:
            modes[bone_name] = current_mode
        else:
            modes[bone_name] = DEFAULT_EULER_ORDER
            fallback_names.add(bone_name)
    return modes, fallback_names


def _restore_action_slot(
    animation_data,
    action,
    object_name,
    slot_identifier=None,
    slot_handle=None,
):
    animation_data.action = action
    _set_action_slot(
        animation_data,
        action,
        object_name,
        slot_identifier=slot_identifier,
        slot_handle=slot_handle,
    )


def _remove_fcurve(channelbag, action, fcurve):
    if channelbag is not None:
        channelbag.fcurves.remove(fcurve)
    else:
        action.fcurves.remove(fcurve)


def _make_quaternion_action(source_action, rig, rotation_modes, overwrite=False):
    """Copy or overwrite an Action and replace relevant Euler channels."""
    euler_curves, quaternion_curves, _axis_angle_curves = _action_rotation_curves(source_action)
    selected_bones = sorted(_convertible_bones(source_action, rig))
    if not selected_bones:
        return None, {}, []

    new_action = source_action if overwrite else source_action.copy()
    if not overwrite:
        new_action.name = _unique_action_name(f"{source_action.name}{ACTION_SUFFIX}")
    destination_euler_curves, destination_quaternion_curves, _destination_axis_angle_curves = (
        _action_rotation_curves(new_action)
    )
    converted_counts = {}
    skipped = []

    for bone_name in selected_bones:
        source_bone_curves = euler_curves[bone_name]
        source_by_index = _curve_by_index(source_bone_curves)
        frames = _keyframe_frames(source_bone_curves)
        if not frames:
            skipped.append((bone_name, "no keyframes"))
            continue

        copied_bone_curves = destination_euler_curves.get(bone_name, [])
        if not copied_bone_curves:
            skipped.append((bone_name, "copied Euler curves not found"))
            continue
        target_channelbag = copied_bone_curves[0][0]
        extrapolation = getattr(copied_bone_curves[0][1], "extrapolation", "CONSTANT")

        rotation_mode = rotation_modes.get(bone_name, DEFAULT_EULER_ORDER)
        if rotation_mode not in EULER_ORDERS:
            rotation_mode = DEFAULT_EULER_ORDER
        samples = []
        previous_quaternion = None
        for frame in frames:
            euler_values = [
                source_by_index[index].evaluate(frame)
                if index in source_by_index
                else 0.0
                for index in range(3)
            ]
            quaternion = Euler(euler_values, rotation_mode).to_quaternion()
            values = [float(value) for value in quaternion]

            # q and -q represent the same orientation. Keep adjacent keys on
            # one hemisphere so interpolation does not make a needless flip.
            if previous_quaternion is not None:
                dot = sum(a * b for a, b in zip(previous_quaternion, values))
                if dot < 0.0:
                    values = [-value for value in values]
            samples.append((frame, values))
            previous_quaternion = values

        for channelbag, fcurve in list(copied_bone_curves):
            _remove_fcurve(channelbag, new_action, fcurve)
        for channelbag, fcurve in list(destination_quaternion_curves.get(bone_name, [])):
            _remove_fcurve(channelbag, new_action, fcurve)

        fcurve_collection = (
            target_channelbag.fcurves
            if target_channelbag is not None
            else new_action.fcurves
        )
        data_path = f'pose.bones["{bone_name}"].rotation_quaternion'
        new_curves = [
            fcurve_collection.new(data_path=data_path, index=index)
            for index in range(4)
        ]
        for fcurve in new_curves:
            fcurve.extrapolation = extrapolation

        for frame, values in samples:
            for index, value in enumerate(values):
                point = new_curves[index].keyframe_points.insert(
                    frame,
                    value,
                    options={"FAST"},
                )
                point.interpolation = "BEZIER"
            previous_quaternion = values

        converted_counts[bone_name] = len(frames)

    for _channelbag, fcurve in _iter_action_fcurves(new_action):
        if fcurve.data_path.endswith("rotation_quaternion"):
            fcurve.update()
    try:
        new_action.update_tag()
    except AttributeError:
        new_action.update()
    return new_action, converted_counts, skipped


def _set_converted_rotation_modes(rig, converted_bones):
    for bone_name in converted_bones:
        pose_bone = rig.pose.bones.get(bone_name)
        if pose_bone:
            pose_bone.rotation_mode = "QUATERNION"
    for bone_name in CLAVICLE_NAMES:
        pose_bone = rig.pose.bones.get(bone_name)
        if pose_bone:
            pose_bone.rotation_mode = "XYZ"


def _action_items_for_rig(rig):
    items = []
    for action in bpy.data.actions:
        bones = _convertible_bones(action, rig)
        if not bones:
            continue
        items.append((action.name, _rotation_key_count(action, rig)))
    return items


def _sync_action_items(scene, rig, preserve_selection=True):
    old_selection = {}
    if preserve_selection:
        old_selection = {
            item.action_name: bool(item.selected)
            for item in scene.rigify_quat_action_items
        }
    active_action = _active_action(rig)
    active_name = active_action.name if active_action else ""
    items = _action_items_for_rig(rig) if rig else []
    items.sort(key=lambda item: (0 if item[0] == active_name else 1, item[0].casefold()))

    scene.rigify_quat_action_items.clear()
    for action_name, key_count in items:
        item = scene.rigify_quat_action_items.add()
        item.action_name = action_name
        item.key_count = key_count
        item.selected = old_selection.get(action_name, action_name == active_name)
    scene.rigify_quat_action_index = min(
        scene.rigify_quat_action_index,
        max(0, len(scene.rigify_quat_action_items) - 1),
    )
    return len(items)


def _tag_redraw():
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            area.tag_redraw()


def _refresh_current_file_action_list(preserve_selection=False):
    rig = _target_rig()
    scenes = tuple(
        scene
        for scene in bpy.data.scenes
        if hasattr(scene, "rigify_quat_action_items")
    )
    if rig is None or not scenes:
        return 0
    counts = []
    for scene in scenes:
        count = _sync_action_items(
            scene,
            rig,
            preserve_selection=preserve_selection,
        )
        counts.append(count)
        if hasattr(scene, "rigify_quat_status"):
            scene.rigify_quat_status = (
                f"พบ {count} Action ที่มี Euler control ให้แปลง" if count else ""
            )
    _tag_redraw()
    return counts[0] if len(counts) == 1 else sum(counts)


def _deferred_refresh_action_list():
    try:
        _refresh_current_file_action_list(preserve_selection=False)
    except Exception as error:
        print(f"ScriptToolkit Quaternion Converter deferred refresh failed: {error}")
    return None


@persistent
def _rigify_quat_load_post(_dummy):
    try:
        _refresh_current_file_action_list(preserve_selection=False)
    except Exception as error:
        print(f"ScriptToolkit Quaternion Converter load refresh failed: {error}")


def _remove_load_post_handler():
    for handler in list(bpy.app.handlers.load_post):
        if (
            getattr(handler, "__name__", None) == "_rigify_quat_load_post"
            and getattr(handler, "__module__", None) == __name__
        ):
            bpy.app.handlers.load_post.remove(handler)


class AQ_ActionItem(PropertyGroup):
    action_name: StringProperty(name="Action")
    selected: BoolProperty(name="Convert", default=False)
    key_count: IntProperty(name="Rotation Keys", default=0, min=0)


class AQ_UL_action_list(UIList):
    def filter_items(self, _context, data, property_name):
        items = getattr(data, property_name)
        if not self.filter_name:
            return [], []
        flags = UI_UL_list.filter_items_by_name(
            self.filter_name,
            self.bitflag_filter_item,
            items,
            "action_name",
            reverse=self.use_filter_invert,
        )
        return flags, []

    def draw_item(
        self,
        _context,
        layout,
        _data,
        item,
        _icon,
        _active_data,
        _active_property,
        _index,
    ):
        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        row.label(text=item.action_name, icon="ACTION")
        if item.key_count:
            row.label(text=f"{item.key_count} keys")


class AQ_OT_set_meta_quaternion_and_regenerate(Operator):
    bl_idname = "script_toolkit.rigify_set_meta_quaternion_regenerate"
    bl_label = "Convert Meta Bones + Re-Generate Rig"
    bl_description = (
        f"Set {METARIG_NAME} bones to Quaternion except the two clavicles, "
        f"then overwrite {RIG_NAME} with Rigify"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, _context):
        return _metarig() is not None and _target_rig() is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        metarig = _metarig()
        target_rig = _target_rig()
        if metarig is None:
            self.report({"ERROR"}, f"ไม่พบ Armature ชื่อ {METARIG_NAME}")
            return {"CANCELLED"}
        if target_rig is None:
            self.report({"ERROR"}, f"ไม่พบ generated Rig ชื่อ {RIG_NAME}")
            return {"CANCELLED"}
        if not hasattr(metarig.data, "rigify_target_rig"):
            self.report({"ERROR"}, "Rigify metarig property rigify_target_rig ไม่พร้อมใช้งาน")
            return {"CANCELLED"}

        state = _capture_context(context)
        animation_state = _capture_animation_data(target_rig)
        try:
            if context.object and context.object.mode != "OBJECT":
                if bpy.ops.object.mode_set.poll():
                    bpy.ops.object.mode_set(mode="OBJECT")

            bpy.ops.object.select_all(action="DESELECT")
            metarig.select_set(True)
            context.view_layer.objects.active = metarig
            if metarig.mode != "POSE" and bpy.ops.object.mode_set.poll():
                bpy.ops.object.mode_set(mode="POSE")

            metarig.data.rigify_target_rig = target_rig
            quaternion_count, clavicle_count = _set_metarig_rotation_modes(metarig)
            if not bpy.ops.pose.rigify_generate.poll():
                raise RuntimeError(
                    "Rigify Generate ใช้งานไม่ได้ใน context ปัจจุบัน; "
                    "ตรวจว่าเปิด Rigify และเลือก metarig อยู่"
                )

            result = bpy.ops.pose.rigify_generate()
            if "FINISHED" not in result:
                raise RuntimeError(f"Rigify Generate คืนค่า {result}")
        except Exception as error:
            recovery_rig = _target_rig()
            try:
                _restore_animation_data(recovery_rig, animation_state)
                if recovery_rig is not None:
                    _sync_action_items(context.scene, recovery_rig)
            except Exception as restore_error:
                print(f"ScriptToolkit Quaternion Converter recovery failed: {restore_error}")
            _tag_redraw()
            _restore_context(context, state)
            self.report({"ERROR"}, f"ตั้งค่า Meta/Generate Rig ไม่สำเร็จ: {error}")
            return {"CANCELLED"}

        generated_rig = _live_object(_safe_get(metarig.data, "rigify_target_rig"))
        generated_rig = generated_rig or _target_rig()
        if generated_rig is None:
            _restore_context(context, state)
            self.report({"ERROR"}, f"Rigify Generate แล้วไม่พบ {RIG_NAME}")
            return {"CANCELLED"}

        restored_animation = _restore_animation_data(generated_rig, animation_state)
        list_count = _sync_action_items(
            context.scene,
            generated_rig,
            preserve_selection=True,
        )
        _tag_redraw()
        _restore_context(context, state)
        generated_name = generated_rig.name if generated_rig else RIG_NAME
        animation_note = (
            f"คืน Active Action: {'สำเร็จ' if restored_animation['active_action'] else 'ไม่มีข้อมูล'}; "
            f"คืน NLA {restored_animation['nla_strips']} strip"
        )
        if restored_animation["missing_actions"]:
            animation_note += (
                f"; ข้าม {restored_animation['missing_actions']} strip "
                "เพราะไม่พบ Action ต้นฉบับ"
            )
        context.scene.rigify_quat_status = (
            f"Generate สำเร็จ; {animation_note}; พบ {list_count} Action"
        )
        self.report(
            {"INFO"},
            f"ตั้ง {quaternion_count} bones เป็น Quaternion, "
            f"ยกเว้น clavicle {clavicle_count} bones เป็น XYZ; "
            f"Rigify Generate สำเร็จ: {generated_name}; {animation_note}; "
            f"พบ {list_count} Action",
        )
        return {"FINISHED"}


class AQ_OT_refresh_actions(Operator):
    bl_idname = "script_toolkit.rigify_quat_refresh_actions"
    bl_label = "Refresh Animation List"
    bl_description = f"Refresh Actions that contain Euler controls for {RIG_NAME}"

    def execute(self, context):
        rig = _target_rig()
        if rig is None:
            self.report({"ERROR"}, f"ไม่พบ Armature ชื่อ {RIG_NAME}")
            return {"CANCELLED"}
        count = _sync_action_items(context.scene, rig)
        context.scene.rigify_quat_status = f"พบ {count} Action ที่มี Euler control ให้แปลง"
        self.report({"INFO"}, context.scene.rigify_quat_status)
        return {"FINISHED"}


class AQ_OT_select_actions(Operator):
    bl_idname = "script_toolkit.rigify_quat_select_actions"
    bl_label = "Select Actions"

    mode: EnumProperty(
        items=(
            ("ALL", "All", "เลือกทุก Action ในรายการ"),
            ("NONE", "None", "ไม่เลือก Action ใด"),
            ("INVERT", "Invert", "กลับสถานะการเลือก"),
        ),
        options={"HIDDEN"},
    )

    def execute(self, context):
        for item in context.scene.rigify_quat_action_items:
            if self.mode == "ALL":
                item.selected = True
            elif self.mode == "NONE":
                item.selected = False
            else:
                item.selected = not item.selected
        return {"FINISHED"}


class AQ_OT_convert_actions(Operator):
    bl_idname = "script_toolkit.rigify_quat_convert_actions"
    bl_label = "Convert Animation to Quaternion"
    bl_description = (
        f"Create _QUAT Actions or overwrite source Actions for {RIG_NAME}"
    )
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        if context.scene.rigify_quat_overwrite_source:
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        scene = context.scene
        rig = _target_rig()
        if rig is None:
            self.report({"ERROR"}, f"ไม่พบ Armature ชื่อ {RIG_NAME}")
            return {"CANCELLED"}

        active_only = bool(scene.rigify_quat_active_only)
        overwrite_source = bool(scene.rigify_quat_overwrite_source)
        active_action = _active_action(rig)
        if active_only:
            if active_action is None:
                self.report({"ERROR"}, f"{RIG_NAME} ไม่มี Active Action")
                return {"CANCELLED"}
            source_actions = [active_action]
        else:
            source_actions = [
                action
                for item in scene.rigify_quat_action_items
                if item.selected
                for action in [bpy.data.actions.get(item.action_name)]
                if action is not None
            ]
            if not source_actions:
                self.report({"ERROR"}, "กรุณาติ๊กเลือก Action อย่างน้อยหนึ่งรายการ")
                return {"CANCELLED"}

        rotation_modes, fallback_names = _resolve_rotation_modes(
            scene,
            rig,
            source_actions,
        )
        converted_actions = []
        failures = []
        active_result = None
        total_bones = 0

        for source_action in source_actions:
            if not _convertible_bones(source_action, rig):
                failures.append(f"{source_action.name}: ไม่มี Euler control")
                continue
            existing_action_names = {action.name for action in bpy.data.actions}
            try:
                new_action, converted_counts, skipped = _make_quaternion_action(
                    source_action,
                    rig,
                    rotation_modes,
                    overwrite=overwrite_source,
                )
            except Exception as error:
                if not overwrite_source:
                    for partial_action in list(bpy.data.actions):
                        if (
                            partial_action.name not in existing_action_names
                            and partial_action.name.startswith(
                                f"{source_action.name}{ACTION_SUFFIX}"
                            )
                            and partial_action.users == 0
                        ):
                            bpy.data.actions.remove(partial_action)
                failures.append(f"{source_action.name}: {error}")
                continue

            if new_action is None or not converted_counts:
                if (
                    new_action is not None
                    and not overwrite_source
                    and new_action.users == 0
                ):
                    bpy.data.actions.remove(new_action)
                failures.append(f"{source_action.name}: แปลงไม่ได้")
                continue

            converted_actions.append(new_action)
            total_bones += len(converted_counts)
            _set_converted_rotation_modes(rig, converted_counts)
            if source_action == active_action and not overwrite_source:
                active_result = new_action
            if skipped:
                failures.extend(
                    f"{source_action.name}/{bone_name}: {reason}"
                    for bone_name, reason in skipped
                )

        if active_result is not None:
            animation_data = rig.animation_data_create()
            _restore_action_slot(animation_data, active_result, rig.name)

        if converted_actions:
            names = ", ".join(action.name for action in converted_actions[:3])
            if len(converted_actions) > 3:
                names += f" และอีก {len(converted_actions) - 3} รายการ"
            if overwrite_source:
                assignment_note = " Source Action เดิมถูก overwrite แล้ว; ไม่มีสำเนาใหม่ถูกสร้าง."
            elif active_result is not None:
                assignment_note = " Active Action ถูกสลับเป็นผลลัพธ์ใหม่แล้ว."
            else:
                assignment_note = " Action ที่ไม่ใช่ Active ถูกสร้างไว้ในรายการ Actions แต่ยังไม่ถูก assign ให้ Rig."
            action_verb = "overwrite" if overwrite_source else "สร้าง"
            scene.rigify_quat_status = (
                f"{action_verb} {len(converted_actions)} Action: {names} | "
                f"แปลง {total_bones} control bone.{assignment_note}"
            )
            if fallback_names:
                scene.rigify_quat_status += (
                    f" ใช้ Euler order เริ่มต้น XYZ กับ {len(fallback_names)} bone "
                    "เพราะไม่พบ state เดิม."
                )
            _sync_action_items(scene, rig)
            if failures:
                self.report({"WARNING"}, scene.rigify_quat_status)
            else:
                self.report({"INFO"}, scene.rigify_quat_status)
            return {"FINISHED"}

        scene.rigify_quat_status = "ไม่สร้าง Action ใหม่: ตรวจว่าเลือก Action ที่มี Euler control ของ Rig นี้"
        if failures:
            scene.rigify_quat_status += f" ({failures[0]})"
        self.report({"ERROR"}, scene.rigify_quat_status)
        return {"CANCELLED"}


def draw_ui(layout, context):
    scene = context.scene
    rig = _target_rig()
    metarig = _metarig()
    box = layout.box()
    box.label(text="Rigify Animation → Quaternion", icon="ACTION")
    box.label(text=f"Target: {RIG_NAME}", icon="ARMATURE_DATA")

    if rig is None:
        box.label(text=f"ไม่พบ Armature ชื่อ {RIG_NAME}", icon="ERROR")
        return
    if metarig is None:
        box.label(text=f"ไม่พบ Armature ชื่อ {METARIG_NAME}", icon="ERROR")
        return

    setup_row = box.row()
    setup_row.scale_y = 1.2
    setup_row.operator(
        AQ_OT_set_meta_quaternion_and_regenerate.bl_idname,
        text="Convert Meta Bones + Re-Generate Rig",
        icon="ARMATURE_DATA",
    )

    active_action = _active_action(rig)
    active_name = active_action.name if active_action else "ไม่มี Active Action"
    active_row = box.row()
    active_row.label(text=f"Active: {active_name}", icon="PLAY")

    options = box.column(align=True)
    options.prop(scene, "rigify_quat_active_only", text="Run Active Action Only")
    options.prop(
        scene,
        "rigify_quat_overwrite_source",
        text="Overwrite Source Action",
    )
    if scene.rigify_quat_overwrite_source:
        options.label(
            text="โหมดนี้จะแก้ Action ต้นฉบับโดยตรง และไม่สามารถย้อนกลับผ่านสำเนาได้.",
            icon="ERROR",
        )
    options.label(
        text="Clavicle.L/R จะคง rotation mode เป็น XYZ; control อื่นที่แปลงจะเป็น Quaternion.",
        icon="INFO",
    )

    list_box = box.box()
    list_header = list_box.row(align=True)
    list_header.label(text=f"Actions ({len(scene.rigify_quat_action_items)})", icon="ACTION")
    list_header.operator(AQ_OT_refresh_actions.bl_idname, text="", icon="FILE_REFRESH")
    list_row = list_box.row()
    list_row.enabled = not scene.rigify_quat_active_only
    list_row.template_list(
        AQ_UL_action_list.__name__,
        "",
        scene,
        "rigify_quat_action_items",
        scene,
        "rigify_quat_action_index",
        rows=8,
    )
    select_row = list_box.row(align=True)
    select_row.enabled = not scene.rigify_quat_active_only
    for mode, label in (("ALL", "All"), ("NONE", "None"), ("INVERT", "Invert")):
        operator = select_row.operator(AQ_OT_select_actions.bl_idname, text=label)
        operator.mode = mode

    run_row = box.row()
    run_row.scale_y = 1.25
    run_row.operator(AQ_OT_convert_actions.bl_idname, text="Run", icon="PLAY")

    if scene.rigify_quat_status:
        box.label(text=scene.rigify_quat_status, icon="INFO")


CLASSES = (
    AQ_ActionItem,
    AQ_UL_action_list,
    AQ_OT_set_meta_quaternion_and_regenerate,
    AQ_OT_refresh_actions,
    AQ_OT_select_actions,
    AQ_OT_convert_actions,
)


def register():
    _remove_load_post_handler()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.rigify_quat_action_items = CollectionProperty(type=AQ_ActionItem)
    bpy.types.Scene.rigify_quat_action_index = IntProperty(default=0)
    bpy.types.Scene.rigify_quat_active_only = BoolProperty(
        name="Run Active Action Only",
        description="Convert only the Action currently active on the target Rig",
        default=True,
    )
    bpy.types.Scene.rigify_quat_overwrite_source = BoolProperty(
        name="Overwrite Source Action",
        description="Replace Euler curves in the source Action instead of creating a _QUAT copy",
        default=False,
    )
    bpy.types.Scene.rigify_quat_status = StringProperty(
        name="Quaternion Converter Status",
        default="",
        options={"HIDDEN"},
    )
    bpy.app.handlers.load_post.append(_rigify_quat_load_post)
    try:
        bpy.app.timers.register(_deferred_refresh_action_list, first_interval=0.1)
    except (AttributeError, RuntimeError, TypeError):
        pass


def unregister():
    _remove_load_post_handler()
    for property_name in (
        "rigify_quat_status",
        "rigify_quat_overwrite_source",
        "rigify_quat_active_only",
        "rigify_quat_action_index",
        "rigify_quat_action_items",
    ):
        if hasattr(bpy.types.Scene, property_name):
            delattr(bpy.types.Scene, property_name)
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, TypeError):
            pass
