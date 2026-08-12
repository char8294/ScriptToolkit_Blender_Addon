"""Build and export a compact Auto-Rig Pro-compatible bone mapping preset."""

import difflib
import json
import os
import re
import time
from typing import NamedTuple

import blf
import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, PropertyGroup, UIList, UI_UL_list


_SCENE_PROPERTIES = (
    "arp_retarget_source_armature",
    "arp_retarget_target_armature",
    "arp_retarget_mapping_items",
    "arp_retarget_mapping_index",
    "arp_retarget_selection_anchor",
    "arp_retarget_inline_edit_index",
    "arp_retarget_find",
    "arp_retarget_replace",
    "arp_retarget_prefix",
    "arp_retarget_suffix",
    "arp_retarget_preset_items",
    "arp_retarget_preset_selection",
)

_IK_AXES = (
    ("X", "X", "X"),
    ("Y", "Y", "Y"),
    ("Z", "Z", "Z"),
    ("-X", "-X", "-X"),
    ("-Y", "-Y", "-Y"),
    ("-Z", "-Z", "-Z"),
)

_LIST_LABEL_SPACE = "\u00a0"
_LIST_REGION_SIDE_PADDING = 32.0
_LIST_LABEL_END_MARGIN = 40.0
_LIST_FONT_SIZE = 11
_LIST_LABEL_ELLIPSIS = "…"

_TARGET_DOUBLE_CLICK_SECONDS = 0.4
_AUTOMATIC_MATCH_MIN_SCORE = 0.52
_last_target_click_index = -1
_last_target_click_time = 0.0


def _left_aligned_operator_text(context, text):
    """Fit and pad an operator label so Blender draws it from the left edge."""
    text = text or "None"
    if context is None or context.region is None:
        return text

    ui_scale = context.preferences.system.ui_scale
    blf.size(0, max(1, round(_LIST_FONT_SIZE * ui_scale)))
    cell_width = max(
        80.0,
        (context.region.width - (_LIST_REGION_SIDE_PADDING * ui_scale)) * 0.5,
    )
    content_width = max(24.0, cell_width - (_LIST_LABEL_END_MARGIN * ui_scale))
    text_width = blf.dimensions(0, text)[0]
    if text_width > content_width:
        ellipsis_width = blf.dimensions(0, _LIST_LABEL_ELLIPSIS)[0]
        available_width = max(0.0, content_width - ellipsis_width)
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if blf.dimensions(0, text[:middle])[0] <= available_width:
                low = middle
            else:
                high = middle - 1
        text = text[:low] + _LIST_LABEL_ELLIPSIS
        text_width = blf.dimensions(0, text)[0]

    space_width = max(1.0, blf.dimensions(0, _LIST_LABEL_SPACE)[0])
    padding_width = max(0.0, content_width - text_width)
    return text + (_LIST_LABEL_SPACE * int(padding_width / space_width))


def _reset_target_click_state():
    global _last_target_click_index, _last_target_click_time
    _last_target_click_index = -1
    _last_target_click_time = 0.0


def _is_target_double_click(index, event, now=None):
    global _last_target_click_index, _last_target_click_time
    current_time = time.monotonic() if now is None else now
    has_modifier = bool(event.shift or event.ctrl or event.alt)
    is_double = (
        not has_modifier
        and (
            event.value == "DOUBLE_CLICK"
            or (
                index == _last_target_click_index
                and current_time - _last_target_click_time <= _TARGET_DOUBLE_CLICK_SECONDS
            )
        )
    )
    if is_double or has_modifier:
        _reset_target_click_state()
    else:
        _last_target_click_index = index
        _last_target_click_time = current_time
    return is_double

_MAPPING_STATE_PROPERTIES = (
    "target_name",
    "target_manual",
    "selected",
    "set_as_root",
    "location",
    "ik",
    "ik_pole",
    "ik_world",
    "ik_auto_pole",
    "ik_create_constraints",
    "ik_axis_correction",
    "rot_add",
    "loc_add",
    "loc_mult",
)


def _armature_poll(_self, obj):
    return obj.type == "ARMATURE"


def _tokens(name):
    aliases = {"left": "l", "right": "r", "lft": "l", "rgt": "r"}
    values = []
    for token in re.findall(r"[A-Za-z0-9]+", name.lower()):
        if token == "def":
            continue
        values.append(aliases.get(token, token))
    return values


class _NameSignature(NamedTuple):
    tokens: tuple
    token_set: frozenset
    canonical: str
    side: str | None


def _name_signature(name):
    tokens = tuple(_tokens(name))
    values = frozenset(tokens)
    side = None
    if "l" in values and "r" not in values:
        side = "l"
    elif "r" in values and "l" not in values:
        side = "r"
    return _NameSignature(tokens, values, " ".join(sorted(tokens)), side)


def _match_score(source, target):
    if not source.tokens or not target.tokens:
        return 0.0

    if source.side and target.side and source.side != target.side:
        return 0.0

    common = len(source.token_set & target.token_set)
    if common == 0:
        return 0.0

    if source.canonical == target.canonical:
        return 1.0

    # A source bone may carry an import prefix while the target carries an
    # extra rig prefix. Treat either name as a useful subset of the other.
    subset_ratio = common / max(1, min(len(source.token_set), len(target.token_set)))
    jaccard = common / max(1, len(source.token_set | target.token_set))
    sequence = difflib.SequenceMatcher(None, source.canonical, target.canonical).ratio()
    if source.token_set <= target.token_set or target.token_set <= source.token_set:
        return 0.72 + (subset_ratio * 0.18) + (sequence * 0.10)
    if common < 2:
        return 0.0
    return (jaccard * 0.55) + (sequence * 0.45)


def _ranked_target_candidates(source_signature, target_signatures, minimum_score):
    candidates = []
    for target_name, target_signature in target_signatures.items():
        score = _match_score(source_signature, target_signature)
        if score < minimum_score:
            continue
        sequence = difflib.SequenceMatcher(
            None,
            " ".join(source_signature.tokens),
            " ".join(target_signature.tokens),
        ).ratio()
        candidates.append((score, sequence, target_name))
    candidates.sort(
        key=lambda candidate: (
            -candidate[0],
            -candidate[1],
            candidate[2].casefold(),
        )
    )
    return candidates


def _find_target(source_signature, target_signatures, assigned):
    for _score, _sequence, target_name in _ranked_target_candidates(
        source_signature,
        target_signatures,
        _AUTOMATIC_MATCH_MIN_SCORE,
    ):
        if target_name not in assigned:
            return target_name
    return ""


def _find_unique_target_matches(requested_names, target_names):
    """Assign the strongest available target to each non-empty requested name."""
    target_signatures = {
        target_name: _name_signature(target_name)
        for target_name in target_names
    }
    candidates = []
    for item_index, requested_name in enumerate(requested_names):
        if not requested_name:
            continue
        requested_signature = _name_signature(requested_name)
        for score, sequence, target_name in _ranked_target_candidates(
            requested_signature,
            target_signatures,
            0.0,
        ):
            candidates.append((score, sequence, item_index, target_name))

    candidates.sort(
        key=lambda candidate: (
            -candidate[0],
            -candidate[1],
            candidate[2],
            candidate[3].casefold(),
        )
    )
    matches = {}
    assigned_targets = set()
    for _score, _sequence, item_index, target_name in candidates:
        if item_index in matches or target_name in assigned_targets:
            continue
        matches[item_index] = target_name
        assigned_targets.add(target_name)
    return matches


def _selected_or_active(scene):
    items = scene.arp_retarget_mapping_items
    selected = [item for item in items if item.selected]
    if selected:
        return selected
    if 0 <= scene.arp_retarget_mapping_index < len(items):
        return [items[scene.arp_retarget_mapping_index]]
    return []


def _unique_names(names):
    """Return non-empty bone names in their original order without duplicates."""
    result = []
    seen = set()
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _active_armature_bone_names(context, armature):
    """Read the active bone from an armature in any Blender mode."""
    if not armature or getattr(context, "active_object", None) != armature:
        return []

    data = getattr(armature, "data", None)
    mode = getattr(context, "mode", "OBJECT")
    names = []
    if mode == "POSE":
        active = getattr(context, "active_pose_bone", None)
        if active:
            names.append(active.name)
        else:
            selected = getattr(context, "selected_pose_bones", ()) or ()
            if selected:
                names.append(selected[0].name)
        # In background contexts active_pose_bone is not always exposed, but
        # the active data bone is still available.
        if not names:
            active = getattr(getattr(data, "bones", ()), "active", None)
            if active:
                names.append(active.name)
    elif mode == "EDIT_ARMATURE":
        bones = getattr(data, "edit_bones", ())
        active = getattr(bones, "active", None)
        if active:
            names.append(active.name)
        else:
            selected = [bone for bone in bones if getattr(bone, "select", False)]
            if selected:
                names.append(selected[0].name)
    else:
        bones = getattr(data, "bones", ())
        active = getattr(bones, "active", None)
        if active:
            names.append(active.name)
        else:
            selected = [bone for bone in bones if getattr(bone, "select", False)]
            if selected:
                names.append(selected[0].name)
    return _unique_names(names)


def _selected_pose_bone_names(armature):
    """Return selected Pose Bone names without relying on the active object."""
    if not armature or getattr(armature, "mode", "") != "POSE":
        return []
    pose_bones = getattr(getattr(armature, "pose", None), "bones", ())
    selected = []
    for pose_bone in pose_bones:
        if getattr(pose_bone, "select", False):
            selected.append(pose_bone.name)
    return _unique_names(selected)


def _activate_object(context, obj):
    """Make an object active while tolerating restricted/fake test contexts."""
    if not obj:
        return False
    try:
        for selected in context.selected_objects:
            selected.select_set(False)
    except (AttributeError, RuntimeError):
        pass
    try:
        obj.select_set(True)
    except (AttributeError, RuntimeError):
        pass
    try:
        context.view_layer.objects.active = obj
    except (AttributeError, RuntimeError):
        try:
            bpy.context.view_layer.objects.active = obj
        except (AttributeError, RuntimeError):
            return False
    return True


def _select_armature_bones(context, armature, names):
    """Select named bones and make the first one active in the viewport."""
    names = _unique_names(names)
    if not armature or armature.type != "ARMATURE" or not names:
        return [], names

    previous_mode = getattr(context, "mode", "OBJECT")
    data_bones = getattr(armature.data, "bones", ())
    supports_object_selection = False
    try:
        supports_object_selection = bool(data_bones and hasattr(data_bones[0], "select"))
    except (IndexError, TypeError):
        pass
    restore_mode = previous_mode if previous_mode in {"POSE", "EDIT_ARMATURE"} else ""
    # Blender 5.x removed Bone.select in Object mode. Enter Pose mode so the
    # selection is visible and behaves like Auto-Rig Pro's Remap controls.
    selection_mode = restore_mode or ("OBJECT" if supports_object_selection else "POSE")
    if previous_mode != "OBJECT":
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except (RuntimeError, TypeError):
            selection_mode = ""

    _activate_object(context, armature)

    found = [data_bones.get(name) for name in names if data_bones.get(name)]
    if selection_mode == "POSE":
        try:
            bpy.ops.object.mode_set(mode="POSE")
        except (RuntimeError, TypeError):
            pass
        pose_bones = getattr(armature.pose, "bones", ())
        for pose_bone in pose_bones:
            pose_bone.select = False
        pose_found = [pose_bones.get(name) for name in names if pose_bones.get(name)]
        # Select the first requested bone last, which makes it the active
        # bone in Blender versions that expose active-bone state implicitly.
        for pose_bone in reversed(pose_found):
            pose_bone.select = True
        if pose_found:
            try:
                data_bones.active = pose_found[0].bone
            except (AttributeError, RuntimeError):
                pass
        found = [pose_bone.bone for pose_bone in pose_found]
    elif selection_mode == "EDIT_ARMATURE":
        try:
            bpy.ops.object.mode_set(mode="EDIT")
        except (RuntimeError, TypeError):
            pass
        edit_bones = getattr(armature.data, "edit_bones", ())
        for bone in edit_bones:
            bone.select = False
        edit_found = [edit_bones.get(name) for name in names if edit_bones.get(name)]
        for bone in reversed(edit_found):
            bone.select = True
        if edit_found:
            try:
                edit_bones.active = edit_found[0]
            except (AttributeError, RuntimeError):
                pass
        found = edit_found
    else:
        for bone in data_bones:
            bone.select = False
        for bone in found:
            bone.select = True
        if found:
            try:
                data_bones.active = found[0]
            except (AttributeError, RuntimeError):
                pass

    return [bone.name for bone in found], [name for name in names if not data_bones.get(name)]


def _add_or_update_mapping_pair(scene, source_name, target_name):
    """Map one Source/Target pair, removing any previous use of the Target."""
    items = scene.arp_retarget_mapping_items
    conflict_indices = [
        index
        for index, item in enumerate(items)
        if item.target_name == target_name and item.source_name != source_name
    ]
    for index in sorted(conflict_indices, reverse=True):
        items.remove(index)

    source_indices = [
        index for index, item in enumerate(items) if item.source_name == source_name
    ]
    if source_indices:
        for index in reversed(source_indices[1:]):
            items.remove(index)
        item = items[source_indices[0]]
        operation = "Updated"
    else:
        item = items.add()
        item.source_name = source_name
        operation = "Added"

    item.target_name = target_name
    item.target_manual = True
    for mapping_item in items:
        mapping_item.selected = False
    item.selected = True
    item_index = next(
        index
        for index, mapping_item in enumerate(items)
        if mapping_item.source_name == source_name
    )
    scene.arp_retarget_mapping_index = item_index
    scene.arp_retarget_selection_anchor = item_index
    scene.arp_retarget_inline_edit_index = -1
    return operation, len(conflict_indices), item_index


def _arp_collection(scene):
    """Return the ARP mapping collection, preferring the current v2 API."""
    for property_name in ("bones_map_v2", "bones_map"):
        try:
            collection = getattr(scene, property_name)
        except (AttributeError, RuntimeError):
            continue
        if collection is not None:
            return property_name, collection
    return "", None


def _set_arp_scene_value(scene, property_name, value):
    """Set an ARP scene property, with an ID-property fallback for callbacks."""
    try:
        setattr(scene, property_name, value)
        return True
    except (AttributeError, RuntimeError, TypeError, ValueError):
        try:
            scene[property_name] = value
            return True
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False


def _set_arp_item_value(item, property_name, value):
    try:
        setattr(item, property_name, value)
        return True
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _clear_collection(collection):
    try:
        collection.clear()
        return
    except (AttributeError, RuntimeError):
        pass
    try:
        for index in range(len(collection) - 1, -1, -1):
            collection.remove(index)
    except (AttributeError, RuntimeError, TypeError):
        pass


def _copy_mapping_to_arp(scene, collection, mapping_items):
    """Copy this editor's rows to ARP's v2 (or legacy) mapping collection."""
    _clear_collection(collection)
    copied = 0
    for index, mapping in enumerate(mapping_items):
        try:
            arp_item = collection.add()
        except (AttributeError, RuntimeError):
            break
        source_set = _set_arp_item_value(arp_item, "source_bone", mapping.source_name)
        target_set = _set_arp_item_value(arp_item, "name", mapping.target_name or "")
        _set_arp_item_value(arp_item, "id", index)
        for source_name, arp_name in (
            ("set_as_root", "set_as_root"),
            ("location", "location"),
            ("ik", "ik"),
            ("ik_pole", "ik_pole"),
            ("ik_world", "ik_world"),
            ("ik_auto_pole", "ik_auto_pole"),
            ("ik_create_constraints", "ik_create_constraints"),
            ("ik_axis_correction", "IK_axis_correc"),
            ("rot_add", "rot_add"),
            ("loc_add", "loc_add"),
            ("loc_mult", "loc_mult"),
        ):
            _set_arp_item_value(arp_item, arp_name, getattr(mapping, source_name))
        if source_set and target_set:
            copied += 1

    try:
        scene.bones_map_index = min(
            max(0, int(scene.arp_retarget_mapping_index)),
            max(0, len(collection) - 1),
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    return copied


def _invoke_arp_build_bones_list(context, scene, source, target):
    """Ask ARP to rebuild its Remap list when its operator is available."""
    action = getattr(getattr(source, "animation_data", None), "action", None)
    action_name = action.name if action else ""
    if action_name and hasattr(scene, "source_action"):
        _set_arp_scene_value(scene, "source_action", action_name)
    _set_arp_scene_value(scene, "source_rig", source.name)
    _set_arp_scene_value(scene, "target_rig", target.name)

    arp_ops = getattr(bpy.ops, "arp", None)
    build_operator = getattr(arp_ops, "build_bones_list", None)
    if build_operator is None:
        return False, "Auto-Rig Pro build_bones_list operator is unavailable"

    if not action_name and not getattr(scene, "source_action", ""):
        return False, "Auto-Rig Pro needs a Source Action to build its list"

    _activate_object(context, source)
    try:
        result = build_operator()
    except (RuntimeError, TypeError, ValueError) as error:
        return False, str(error)
    if result != {"FINISHED"}:
        return False, f"Auto-Rig Pro build_bones_list returned {result}"
    return True, ""


def _mapping_state(item):
    state = {}
    for property_name in _MAPPING_STATE_PROPERTIES:
        value = getattr(item, property_name)
        state[property_name] = tuple(value) if property_name in {"rot_add", "loc_add"} else value
    return state


def _restore_mapping_state(item, state):
    for property_name, value in state.items():
        setattr(item, property_name, value)


def _validated_armatures(operator, scene):
    source = scene.arp_retarget_source_armature
    target = scene.arp_retarget_target_armature
    if not source or source.type != "ARMATURE":
        operator.report({"ERROR"}, "Choose a Source Armature first")
        return None
    if not target or target.type != "ARMATURE":
        operator.report({"ERROR"}, "Choose a Target Armature first")
        return None
    if source == target:
        operator.report({"ERROR"}, "Source and Target Armature must be different")
        return None
    return source, target


def _apply_rename_parts(name, find_text, replace_text, prefix, suffix):
    renamed = name.replace(find_text, replace_text) if find_text else name
    return f"{prefix}{renamed}{suffix}"


def _rename_selected_target_names(scene, derive_from_source):
    items = _selected_or_active(scene)
    if not items:
        return None

    changed = 0
    skipped = 0
    for item in items:
        base_name = item.source_name if derive_from_source else item.target_name
        if not base_name:
            skipped += 1
            continue
        new_name = _apply_rename_parts(
            base_name,
            scene.arp_retarget_find,
            scene.arp_retarget_replace,
            scene.arp_retarget_prefix,
            scene.arp_retarget_suffix,
        )
        if new_name != item.target_name:
            item.target_name = new_name
            item.target_manual = True
            changed += 1
    return changed, skipped


def _select_mapping_row(scene, index, select_range=False, extend=False, deselect=False):
    items = scene.arp_retarget_mapping_items
    if not (0 <= index < len(items)):
        return False

    scene.arp_retarget_inline_edit_index = -1

    anchor = scene.arp_retarget_selection_anchor
    if deselect:
        previous_active = scene.arp_retarget_mapping_index
        items[index].selected = False
        remaining = [item_index for item_index, item in enumerate(items) if item.selected]
        if not remaining:
            scene.arp_retarget_selection_anchor = -1
            scene.arp_retarget_mapping_index = -1
            return True

        nearest_selected = min(remaining, key=lambda item_index: (abs(item_index - index), item_index))
        if previous_active == index or not (0 <= previous_active < len(items)):
            scene.arp_retarget_mapping_index = nearest_selected
        else:
            scene.arp_retarget_mapping_index = previous_active
        if anchor == index:
            scene.arp_retarget_selection_anchor = nearest_selected
        return True
    elif extend:
        items[index].selected = True
        scene.arp_retarget_selection_anchor = index
    elif select_range and 0 <= anchor < len(items):
        for item in items:
            item.selected = False
        start, end = sorted((anchor, index))
        for item_index in range(start, end + 1):
            items[item_index].selected = True
    else:
        for item in items:
            item.selected = False
        items[index].selected = True
        scene.arp_retarget_selection_anchor = index

    scene.arp_retarget_mapping_index = index
    return True


def _mirror_name(name, mirror_dir):
    if not name:
        return ""

    sides = {"l": "r", "left": "right"}
    if mirror_dir == "RIGHT_TO_LEFT":
        sides = {value: key for key, value in sides.items()}

    def mirror_word(word):
        for source, target in sides.items():
            if word == source:
                return target
            if word == source.upper():
                return target.upper()
            if word == source.title():
                return target.title()
        return ""

    parts = re.split(r"([._ \-])", name)
    word_indices = [index for index in range(0, len(parts), 2) if parts[index]]
    if not word_indices:
        return ""
    search_order = (word_indices[-1], word_indices[0], *word_indices[1:-1])
    for word_index in dict.fromkeys(search_order):
        mirrored = mirror_word(parts[word_index])
        if mirrored:
            parts[word_index] = mirrored
            return "".join(parts)
    return ""


def _parse_bool(value):
    return str(value).strip().lower() in {"true", "1", "yes"}


def _parse_vector(value, default=(0.0, 0.0, 0.0)):
    try:
        values = [float(part) for part in str(value).split(",")]
        if len(values) == 3:
            return values
    except (TypeError, ValueError):
        pass
    return list(default)


def _vector_text(value):
    return ",".join(f"{float(part):g}" for part in value)


def _get_target_name_inline(item):
    return item.target_name


def _set_target_name_inline(item, value):
    item.target_name = value
    item.target_manual = True


def _default_preset_directory():
    return os.path.join(
        os.path.expanduser("~"),
        "Documents",
        "AutoRigPro",
        "Remap Presets",
    )


def _preset_files(directory=None):
    directory = directory or _default_preset_directory()
    try:
        entries = os.scandir(directory)
    except OSError:
        return []

    with entries:
        files = [
            (entry.name, entry.path)
            for entry in entries
            if entry.is_file() and entry.name.lower().endswith(".bmap")
        ]
    return sorted(files, key=lambda value: value[0].casefold())


def _refresh_preset_items(scene, directory=None):
    files = _preset_files(directory)
    selected = scene.arp_retarget_preset_selection
    scene.arp_retarget_preset_items.clear()
    for name, filepath in files:
        item = scene.arp_retarget_preset_items.add()
        item.name = name
        item.filepath = filepath
    if selected and selected not in {name for name, _filepath in files}:
        scene.arp_retarget_preset_selection = ""
    return files


def _refresh_all_preset_items(data=None):
    data = bpy.data if data is None else data
    scenes = getattr(data, "scenes", ())
    refreshed = 0
    for scene in scenes:
        _refresh_preset_items(scene)
        refreshed += 1
    return refreshed


def _refresh_preset_items_timer():
    _refresh_all_preset_items()
    return None


def _schedule_preset_items_refresh():
    timers = getattr(bpy.app, "timers", None)
    if timers is None:
        return
    try:
        if not timers.is_registered(_refresh_preset_items_timer):
            timers.register(_refresh_preset_items_timer, first_interval=0.1)
    except (RuntimeError, ValueError):
        pass


def _cancel_preset_items_refresh():
    timers = getattr(bpy.app, "timers", None)
    if timers is None:
        return
    try:
        if timers.is_registered(_refresh_preset_items_timer):
            timers.unregister(_refresh_preset_items_timer)
    except (RuntimeError, ValueError):
        pass


def _selected_preset_filepath(scene, preset_name, directory=None):
    for name, filepath in _preset_files(directory):
        if name == preset_name:
            return filepath
    return ""


class STARP_PresetItem(PropertyGroup):
    name: StringProperty(name="Preset")
    filepath: StringProperty(name="File Path", subtype="FILE_PATH", options={"HIDDEN"})


class STARP_MappingItem(PropertyGroup):
    source_name: StringProperty(name="Source Bone")
    target_name: StringProperty(name="Target Bone", default="")
    target_name_inline: StringProperty(
        name="Target Bone",
        get=_get_target_name_inline,
        set=_set_target_name_inline,
    )
    target_manual: BoolProperty(name="Target Manually Edited", default=True, options={"HIDDEN"})
    selected: BoolProperty(name="Selected", default=False)
    set_as_root: BoolProperty(name="Set as Root", default=False)
    location: BoolProperty(name="Location (Local)", default=False)
    ik: BoolProperty(name="IK", default=False)
    ik_pole: StringProperty(name="IK Pole", default="")
    ik_world: BoolProperty(name="IK World Space", default=False)
    ik_auto_pole: EnumProperty(
        name="IK Auto Pole",
        items=(
            ("ABSOLUTE", "Absolute", "Evaluate the real IK pole position"),
            ("RELATIVE_TARGET", "Relative: Target", "Evaluate the pole relative to the target"),
            ("RELATIVE_CHAIN", "Relative: Chain", "Evaluate the pole relative to the IK chain"),
        ),
        default="ABSOLUTE",
    )
    ik_create_constraints: BoolProperty(name="Add IK Constraints", default=False)
    ik_axis_correction: EnumProperty(
        name="IK Axis Correction",
        items=_IK_AXES,
        default="Y",
    )
    rot_add: FloatVectorProperty(name="Rotation Offset", size=3, default=(0.0, 0.0, 0.0))
    loc_add: FloatVectorProperty(name="Location Offset", size=3, default=(0.0, 0.0, 0.0))
    loc_mult: FloatProperty(name="Location Multiplier", default=1.0)


class STARP_OT_select_mapping_row(Operator):
    bl_idname = "script_toolkit.arp_select_mapping_row"
    bl_label = "Select Mapping Row"
    bl_description = "Click selects one; Shift selects a range; Ctrl adds; Alt removes this row"
    bl_options = {"INTERNAL"}

    index: IntProperty()

    def invoke(self, context, event):
        if not _select_mapping_row(
            context.scene,
            self.index,
            select_range=event.shift,
            extend=event.ctrl,
            deselect=event.alt,
        ):
            return {"CANCELLED"}
        return {"FINISHED"}

    def execute(self, context):
        if not _select_mapping_row(context.scene, self.index):
            return {"CANCELLED"}
        return {"FINISHED"}


class STARP_OT_target_mapping_cell(Operator):
    bl_idname = "script_toolkit.arp_target_mapping_cell"
    bl_label = "Target Bone"
    bl_description = "Click selects one; Shift selects a range; Ctrl adds; Alt removes"
    bl_options = {"INTERNAL"}

    index: IntProperty()

    def invoke(self, context, event):
        is_double = _is_target_double_click(self.index, event)
        if not _select_mapping_row(
            context.scene,
            self.index,
            select_range=event.shift,
            extend=event.ctrl,
            deselect=event.alt,
        ):
            return {"CANCELLED"}
        if is_double:
            context.scene.arp_retarget_inline_edit_index = self.index
            if context.area:
                context.area.tag_redraw()
        return {"FINISHED"}

    def execute(self, context):
        if not _select_mapping_row(context.scene, self.index):
            return {"CANCELLED"}
        return {"FINISHED"}


class STARP_OT_synchro_select(Operator):
    """Select mapping rows for the active Source or Target viewport bone."""

    bl_idname = "script_toolkit.arp_synchro_select"
    bl_label = "Select Viewport Bone in List"
    bl_description = (
        "Find the active Source or Target viewport bone in the mapping list, "
        "like Auto-Rig Pro Remap"
    )
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        source = scene.arp_retarget_source_armature
        target = scene.arp_retarget_target_armature
        active_object = getattr(context, "active_object", None)
        if active_object == source:
            bone_names = _active_armature_bone_names(context, source)
            property_name = "source_name"
            side_name = "Source"
        elif active_object == target:
            bone_names = _active_armature_bone_names(context, target)
            property_name = "target_name"
            side_name = "Target"
        else:
            self.report({"WARNING"}, "Select a Source or Target Armature in the viewport")
            return {"CANCELLED"}

        if not bone_names:
            self.report({"WARNING"}, "Select a bone in the viewport first")
            return {"CANCELLED"}

        matches = [
            index
            for index, item in enumerate(scene.arp_retarget_mapping_items)
            if getattr(item, property_name) in bone_names
        ]
        if not matches:
            self.report({"WARNING"}, f"No mapping row found for {side_name} bone(s)")
            return {"CANCELLED"}

        for item in scene.arp_retarget_mapping_items:
            item.selected = False
        for index in matches:
            scene.arp_retarget_mapping_items[index].selected = True
        scene.arp_retarget_mapping_index = matches[0]
        scene.arp_retarget_selection_anchor = matches[0]
        scene.arp_retarget_inline_edit_index = -1
        if getattr(context, "area", None):
            context.area.tag_redraw()
        self.report({"INFO"}, f"Selected {len(matches)} {side_name} mapping row(s)")
        return {"FINISHED"}


class STARP_OT_select_source_bones(Operator):
    """Select the Source Bones from the currently selected mapping rows."""

    bl_idname = "script_toolkit.arp_select_source_bones"
    bl_label = "Select Source Bones in Viewport"
    bl_description = "Select the mapping list's Source Bones in the viewport"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        source = context.scene.arp_retarget_source_armature
        if not source or source.type != "ARMATURE":
            self.report({"ERROR"}, "Choose a Source Armature first")
            return {"CANCELLED"}
        mapping_items = _selected_or_active(context.scene)
        names = _unique_names(item.source_name for item in mapping_items)
        if not names:
            self.report({"WARNING"}, "Select at least one mapping row")
            return {"CANCELLED"}

        found, missing = _select_armature_bones(context, source, names)
        if not found:
            self.report({"WARNING"}, "None of the selected Source Bones exist in the armature")
            return {"CANCELLED"}
        if missing:
            self.report(
                {"WARNING"},
                f"Selected {len(found)} Source Bones; skipped {len(missing)} missing bone(s)",
            )
        else:
            self.report({"INFO"}, f"Selected {len(found)} Source Bones in the viewport")
        return {"FINISHED"}


class STARP_OT_add_selected_bone_pair(Operator):
    """Add or update one mapping from the selected Source/Target Pose Bones."""

    bl_idname = "script_toolkit.arp_add_selected_bone_pair"
    bl_label = "Add/Update Selected Pair"
    bl_description = (
        "Add one selected Source-to-Target Pose Bone pair; update an existing Source "
        "and remove any previous row using the Target"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        armatures = _validated_armatures(self, scene)
        if armatures is None:
            return {"CANCELLED"}
        source, target = armatures
        if source.mode != "POSE" or target.mode != "POSE":
            self.report(
                {"ERROR"},
                "Put both Source and Target Armatures in Pose Mode first",
            )
            return {"CANCELLED"}

        source_names = _selected_pose_bone_names(source)
        target_names = _selected_pose_bone_names(target)
        if len(source_names) != 1 or len(target_names) != 1:
            self.report(
                {"WARNING"},
                "Select exactly one Source Bone and one Target Bone",
            )
            return {"CANCELLED"}

        operation, removed, _item_index = _add_or_update_mapping_pair(
            scene,
            source_names[0],
            target_names[0],
        )
        message = f"{operation} {source_names[0]} → {target_names[0]}"
        if removed:
            message += f"; removed {removed} previous Target mapping"
        self.report({"INFO"}, message)
        return {"FINISHED"}


class STARP_OT_send_to_arp(Operator):
    """Send the current mapping rows to Auto-Rig Pro's open Remap UI."""

    bl_idname = "script_toolkit.arp_send_to_remap"
    bl_label = "Send to Auto-Rig Pro"
    bl_description = "Copy this mapping list into Auto-Rig Pro Remap"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        armatures = _validated_armatures(self, scene)
        if armatures is None:
            return {"CANCELLED"}
        source, target = armatures

        _collection_name, collection = _arp_collection(scene)
        if collection is None:
            self.report(
                {"ERROR"},
                "Auto-Rig Pro Remap is not available; enable Auto-Rig Pro first",
            )
            return {"CANCELLED"}

        built, build_message = _invoke_arp_build_bones_list(context, scene, source, target)
        copied = _copy_mapping_to_arp(scene, collection, scene.arp_retarget_mapping_items)
        if not copied and scene.arp_retarget_mapping_items:
            self.report({"ERROR"}, "Could not write the mapping rows to Auto-Rig Pro")
            return {"CANCELLED"}

        try:
            scene.bones_map_index = min(
                max(0, int(scene.arp_retarget_mapping_index)),
                max(0, len(collection) - 1),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        for area in getattr(getattr(context, "screen", None), "areas", ()) or ():
            try:
                area.tag_redraw()
            except (AttributeError, RuntimeError):
                pass

        if built:
            self.report({"INFO"}, f"Sent {copied} mapping row(s) to Auto-Rig Pro Remap")
        elif build_message:
            self.report(
                {"INFO"},
                f"Sent {copied} mapping row(s) to Auto-Rig Pro Remap ({build_message})",
            )
        else:
            self.report({"INFO"}, f"Sent {copied} mapping row(s) to Auto-Rig Pro Remap")
        return {"FINISHED"}


class STARP_UL_mapping(UIList):
    def filter_items(self, _context, data, property_name):
        items = getattr(data, property_name)
        if not self.filter_name:
            return [], []
        flags = UI_UL_list.filter_items_by_name(
            self.filter_name,
            self.bitflag_filter_item,
            items,
            "source_name",
            reverse=self.use_filter_invert,
        )
        return flags, []

    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_property, _index):
        split = layout.split(factor=0.5, align=True)
        source = split.operator(
            STARP_OT_select_mapping_row.bl_idname,
            text=_left_aligned_operator_text(_context, item.source_name),
            emboss=item.selected,
            depress=item.selected,
        )
        source.index = _index
        if _context.scene.arp_retarget_inline_edit_index == _index:
            split.prop(item, "target_name_inline", text="", emboss=False)
        else:
            target = split.operator(
                STARP_OT_target_mapping_cell.bl_idname,
                text=_left_aligned_operator_text(_context, item.target_name or "None"),
                emboss=item.selected,
                depress=item.selected,
            )
            target.index = _index


class STARP_OT_pick_selected_armature(Operator):
    bl_idname = "script_toolkit.arp_pick_selected_armature"
    bl_label = "Pick Selected Armature"
    bl_description = "Use the active selected armature"
    bl_options = {"INTERNAL", "UNDO"}

    armature_slot: EnumProperty(
        items=(
            ("SOURCE", "Source", "Assign to Source Armature"),
            ("TARGET", "Target", "Assign to Target Armature"),
        )
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return bool(obj and obj.type == "ARMATURE" and obj.select_get())

    def execute(self, context):
        obj = context.active_object
        property_name = (
            "arp_retarget_source_armature"
            if self.armature_slot == "SOURCE"
            else "arp_retarget_target_armature"
        )
        setattr(context.scene, property_name, obj)
        self.report({"INFO"}, f"Picked {obj.name} as {self.armature_slot.title()} Armature")
        return {"FINISHED"}


class STARP_OT_build_list(Operator):
    bl_idname = "script_toolkit.arp_build_bone_list"
    bl_label = "Build Bone List"
    bl_description = "Create a source-to-target list and guess compatible target names"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        armatures = _validated_armatures(self, scene)
        if armatures is None:
            return {"CANCELLED"}
        source, target = armatures

        # Include every data bone. This editor must expose helper/controller
        # bones too, even when Auto-Rig Pro would filter them during binding.
        source_names = sorted((bone.name for bone in source.data.bones), key=str.casefold)
        target_names = sorted((bone.name for bone in target.data.bones), key=str.casefold)
        target_signatures = {name: _name_signature(name) for name in target_names}
        scene.arp_retarget_mapping_items.clear()

        assigned = set()
        matched = 0
        for source_name in source_names:
            item = scene.arp_retarget_mapping_items.add()
            item.source_name = source_name
            item.target_name = _find_target(_name_signature(source_name), target_signatures, assigned)
            item.target_manual = False
            if item.target_name:
                assigned.add(item.target_name)
                matched += 1

        scene.arp_retarget_mapping_index = 0
        scene.arp_retarget_selection_anchor = -1
        self.report({"INFO"}, f"Built {len(source_names)} source bones; matched {matched} target bones")
        return {"FINISHED"}


class STARP_OT_update_list(Operator):
    bl_idname = "script_toolkit.arp_update_bone_list"
    bl_label = "Update Bone List"
    bl_description = "Merge current armature bones into the list while preserving compatible mappings and settings"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        armatures = _validated_armatures(self, scene)
        if armatures is None:
            return {"CANCELLED"}
        source, target = armatures

        items = scene.arp_retarget_mapping_items
        active_source = (
            items[scene.arp_retarget_mapping_index].source_name
            if 0 <= scene.arp_retarget_mapping_index < len(items)
            else ""
        )
        anchor_source = (
            items[scene.arp_retarget_selection_anchor].source_name
            if 0 <= scene.arp_retarget_selection_anchor < len(items)
            else ""
        )
        existing = {}
        for item in items:
            existing.setdefault(item.source_name, _mapping_state(item))

        source_names = sorted((bone.name for bone in source.data.bones), key=str.casefold)
        source_name_set = set(source_names)
        target_names = sorted((bone.name for bone in target.data.bones), key=str.casefold)
        target_name_set = set(target_names)
        target_signatures = {name: _name_signature(name) for name in target_names}

        preserved_targets = set()
        for source_name in source_names:
            state = existing.get(source_name)
            if state and state["target_name"] in target_name_set:
                preserved_targets.add(state["target_name"])

        scene.arp_retarget_mapping_items.clear()
        added = 0
        preserved = 0
        matched = 0
        assigned = set(preserved_targets)
        for source_name in source_names:
            item = scene.arp_retarget_mapping_items.add()
            item.source_name = source_name
            state = existing.get(source_name)
            if state:
                _restore_mapping_state(item, state)
                if item.target_name in target_name_set or item.target_manual:
                    preserved += 1
                    continue
                item.target_name = ""
            else:
                added += 1

            item.target_name = _find_target(_name_signature(source_name), target_signatures, assigned)
            item.target_manual = False
            if item.target_name:
                assigned.add(item.target_name)
                matched += 1

        removed = len(set(existing) - source_name_set)
        scene.arp_retarget_mapping_index = source_names.index(active_source) if active_source in source_name_set else 0
        scene.arp_retarget_selection_anchor = (
            source_names.index(anchor_source) if anchor_source in source_name_set else -1
        )
        self.report(
            {"INFO"},
            f"Updated {len(source_names)} bones; preserved {preserved}, matched {matched}, added {added}, removed {removed}",
        )
        return {"FINISHED"}


class STARP_OT_match_target_names(Operator):
    bl_idname = "script_toolkit.arp_match_target_names"
    bl_label = "Match Target Names"
    bl_description = (
        "Match non-empty Target Bone names to the closest real bones in the "
        "Target Armature without reusing a bone"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        target = scene.arp_retarget_target_armature
        if not target or target.type != "ARMATURE":
            self.report({"ERROR"}, "Choose a Target Armature first")
            return {"CANCELLED"}

        items = scene.arp_retarget_mapping_items
        requested_names = [item.target_name for item in items]
        attempted = sum(bool(name) for name in requested_names)
        if not attempted:
            self.report({"WARNING"}, "No Target Bone names to match")
            return {"CANCELLED"}

        target_names = sorted(
            (bone.name for bone in target.data.bones),
            key=str.casefold,
        )
        matches = _find_unique_target_matches(requested_names, target_names)
        changed = 0
        for item_index, target_name in matches.items():
            item = items[item_index]
            if item.target_name != target_name:
                item.target_name = target_name
                changed += 1
            item.target_manual = True

        unmatched = attempted - len(matches)
        self.report(
            {"INFO"},
            f"Matched {len(matches)} target names; changed {changed}, unmatched {unmatched}",
        )
        return {"FINISHED"}


class STARP_OT_remove_duplicate_targets(Operator):
    bl_idname = "script_toolkit.arp_remove_duplicate_targets"
    bl_label = "Remove Duplicate Targets"
    bl_description = (
        "Keep the first mapping for each non-empty Target Bone and clear later duplicates"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        seen_targets = set()
        removed = 0
        for item in context.scene.arp_retarget_mapping_items:
            target_name = item.target_name
            if not target_name:
                continue
            if target_name in seen_targets:
                item.target_name = ""
                item.target_manual = True
                removed += 1
                continue
            seen_targets.add(target_name)

        self.report(
            {"INFO"},
            f"Removed {removed} duplicate Target Bone entr{'y' if removed == 1 else 'ies'}",
        )
        return {"FINISHED"}


class STARP_OT_select_all(Operator):
    bl_idname = "script_toolkit.arp_select_all"
    bl_label = "Select All"
    bl_options = {"UNDO"}

    def execute(self, context):
        for item in context.scene.arp_retarget_mapping_items:
            item.selected = True
        context.scene.arp_retarget_selection_anchor = -1
        return {"FINISHED"}


class STARP_OT_select_none(Operator):
    bl_idname = "script_toolkit.arp_select_none"
    bl_label = "Select None"
    bl_options = {"UNDO"}

    def execute(self, context):
        for item in context.scene.arp_retarget_mapping_items:
            item.selected = False
        context.scene.arp_retarget_selection_anchor = -1
        return {"FINISHED"}


class STARP_OT_select_invert(Operator):
    bl_idname = "script_toolkit.arp_select_invert"
    bl_label = "Invert Selection"
    bl_options = {"UNDO"}

    def execute(self, context):
        for item in context.scene.arp_retarget_mapping_items:
            item.selected = not item.selected
        context.scene.arp_retarget_selection_anchor = -1
        return {"FINISHED"}


class STARP_OT_clear_target(Operator):
    bl_idname = "script_toolkit.arp_clear_target"
    bl_label = "Clear Target"
    bl_description = "Clear target names for all checked rows, or the active row if none are checked"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        items = _selected_or_active(context.scene)
        if not items:
            self.report({"WARNING"}, "Select at least one mapping row")
            return {"CANCELLED"}
        for item in items:
            item.target_name = ""
            item.target_manual = True
        self.report({"INFO"}, f"Cleared {len(items)} target names")
        return {"FINISHED"}


class STARP_OT_swap_source_target(Operator):
    bl_idname = "script_toolkit.arp_swap_source_target"
    bl_label = "Swap Source / Target"
    bl_description = "Swap armature roles and reverse every source-to-target mapping"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        old_source = scene.arp_retarget_source_armature
        old_target = scene.arp_retarget_target_armature
        if not old_source or not old_target:
            self.report({"ERROR"}, "Choose Source and Target Armatures first")
            return {"CANCELLED"}

        mapping_properties = tuple(
            name
            for name in _MAPPING_STATE_PROPERTIES
            if name not in {"target_name", "target_manual", "selected"}
        )
        reverse_mapping = {}
        for item in scene.arp_retarget_mapping_items:
            if not item.target_name:
                continue
            values = {}
            for name in mapping_properties:
                value = getattr(item, name)
                values[name] = tuple(value) if name in {"rot_add", "loc_add"} else value
            values["target_name"] = item.source_name
            values["target_manual"] = True
            reverse_mapping[item.target_name] = values

        scene.arp_retarget_source_armature = old_target
        scene.arp_retarget_target_armature = old_source
        scene.arp_retarget_mapping_items.clear()
        reversed_count = 0
        for source_name in sorted((bone.name for bone in old_target.data.bones), key=str.casefold):
            item = scene.arp_retarget_mapping_items.add()
            item.source_name = source_name
            item.target_manual = False
            values = reverse_mapping.get(source_name)
            if values:
                for name, value in values.items():
                    setattr(item, name, value)
                reversed_count += 1

        scene.arp_retarget_mapping_index = 0
        scene.arp_retarget_selection_anchor = -1
        self.report(
            {"INFO"},
            f"Swapped armatures; reversed {reversed_count} mappings across {len(old_target.data.bones)} source bones",
        )
        return {"FINISHED"}


class STARP_OT_mirror_bone_list(Operator):
    bl_idname = "script_toolkit.arp_mirror_bone_list"
    bl_label = "Mirror Bone List"
    bl_description = "Mirror the mapping list from left to right or right to left like Auto-Rig Pro"
    bl_options = {"REGISTER", "UNDO"}

    mirror_dir: EnumProperty(
        name="Direction",
        items=(
            ("LEFT_TO_RIGHT", "Left to Right", "Copy left mappings to their right-side partners"),
            ("RIGHT_TO_LEFT", "Right to Left", "Copy right mappings to their left-side partners"),
        ),
        default="LEFT_TO_RIGHT",
    )

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, _context):
        self.layout.prop(self, "mirror_dir", expand=True)

    def execute(self, context):
        scene = context.scene
        source = scene.arp_retarget_source_armature
        target = scene.arp_retarget_target_armature
        if not source or not target:
            self.report({"ERROR"}, "Choose Source and Target Armatures first")
            return {"CANCELLED"}

        by_source = {item.source_name: item for item in scene.arp_retarget_mapping_items}
        assignments = {}
        for item in scene.arp_retarget_mapping_items:
            mirrored_source = _mirror_name(item.source_name, self.mirror_dir)
            mirrored_target = _mirror_name(item.target_name, self.mirror_dir)
            if not mirrored_source or not mirrored_target:
                continue
            if mirrored_source not in by_source:
                continue
            if not source.data.bones.get(mirrored_source):
                continue
            if not target.data.bones.get(item.target_name) or not target.data.bones.get(mirrored_target):
                continue

            mirrored_pole = _mirror_name(item.ik_pole, self.mirror_dir)
            if not target.data.bones.get(mirrored_pole):
                mirrored_pole = item.ik_pole
            assignments[mirrored_source] = {
                "target_name": mirrored_target,
                "target_manual": True,
                "location": item.location,
                "ik": item.ik,
                "ik_pole": mirrored_pole,
                "ik_world": item.ik_world,
                "ik_auto_pole": item.ik_auto_pole,
                "ik_create_constraints": item.ik_create_constraints,
                "ik_axis_correction": item.ik_axis_correction,
            }

        for source_name, values in assignments.items():
            destination = by_source[source_name]
            for property_name, value in values.items():
                setattr(destination, property_name, value)

        self.report({"INFO"}, f"Mirrored {len(assignments)} mappings")
        return {"FINISHED"}


class STARP_OT_rename_source_to_target(Operator):
    bl_idname = "script_toolkit.arp_rename_source_to_target"
    bl_label = "Rename Source to Target"
    bl_description = "Build Target Bone names from selected Source Bone names"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        result = _rename_selected_target_names(scene, derive_from_source=True)
        if result is None:
            self.report({"WARNING"}, "Select at least one mapping row")
            return {"CANCELLED"}
        changed, _skipped = result
        self.report({"INFO"}, f"Renamed {changed} target names from source names")
        return {"FINISHED"}


class STARP_OT_rename_target(Operator):
    bl_idname = "script_toolkit.arp_rename_target"
    bl_label = "Rename Target"
    bl_description = "Apply Find/Replace and Prefix/Suffix directly to selected Target Bone names"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        result = _rename_selected_target_names(scene, derive_from_source=False)
        if result is None:
            self.report({"WARNING"}, "Select at least one mapping row")
            return {"CANCELLED"}
        changed, skipped = result
        self.report({"INFO"}, f"Renamed {changed} target names; skipped {skipped} empty targets")
        return {"FINISHED"}


class STARP_OT_export_bmap(Operator):
    bl_idname = "script_toolkit.arp_export_bmap"
    bl_label = "Export .bmap Preset"
    bl_description = "Save the mapping in Auto-Rig Pro's .bmap preset format"

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.bmap", options={"HIDDEN"})

    def invoke(self, context, _event):
        self.filepath = "retarget_mapping.bmap"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        filepath = bpy.path.abspath(self.filepath)
        if not filepath.lower().endswith(".bmap"):
            filepath += ".bmap"
        items = context.scene.arp_retarget_mapping_items
        if not items:
            self.report({"WARNING"}, "Build a Bone List before exporting")
            return {"CANCELLED"}
        for item in items:
            if "%" in item.target_name:
                self.report({"ERROR"}, f"Target bone '{item.target_name}' contains '%' which .bmap uses as a delimiter")
                return {"CANCELLED"}

        try:
            parent = os.path.dirname(filepath)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(filepath, "w", encoding="utf-8", newline="\n") as file:
                for item in items:
                    target_name = item.target_name.strip() or "None"
                    first_line = "%".join(
                        (
                            target_name,
                            str(item.location),
                            item.ik_auto_pole,
                            _vector_text(item.rot_add),
                            _vector_text(item.loc_add),
                            f"{item.loc_mult:g}",
                            str(item.ik_create_constraints),
                            str(item.ik_world),
                            item.ik_axis_correction,
                        )
                    )
                    file.write(f"{first_line}%\n")
                    file.write(f"{item.source_name}\n")
                    file.write(f"{item.set_as_root}\n")
                    file.write(f"{item.ik}\n")
                    file.write(f"{item.ik_pole}\n")
        except (OSError, UnicodeError) as error:
            self.report({"ERROR"}, f"Could not write preset: {error}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported {len(items)} mappings to {filepath}")
        return {"FINISHED"}


def _bone_hierarchy_payload(armature):
    """Serialize an armature's complete rest-bone hierarchy for AI tools."""
    bones = sorted(armature.data.bones, key=lambda bone: bone.name.casefold())
    serialized = []
    for bone in bones:
        head = getattr(bone, "head_local", bone.head)
        tail = getattr(bone, "tail_local", bone.tail)
        serialized.append(
            {
                "name": bone.name,
                "parent": bone.parent.name if bone.parent else None,
                "children": sorted(
                    (child.name for child in bone.children),
                    key=str.casefold,
                ),
                "head": [float(value) for value in head],
                "tail": [float(value) for value in tail],
                "length": float(bone.length),
                "use_deform": bool(bone.use_deform),
                "use_connect": bool(bone.use_connect),
            }
        )
    return {
        "name": armature.name,
        "data_name": armature.data.name,
        "bone_count": len(serialized),
        "bones": serialized,
    }


class STARP_OT_export_bone_hierarchy(Operator):
    """Export both configured armature hierarchies in one AI-friendly file."""

    bl_idname = "script_toolkit.arp_export_bone_hierarchy"
    bl_label = "Export Bone Hierarchy"
    bl_description = (
        "Export every Source and Target bone with parent/child hierarchy to one JSON file"
    )

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def invoke(self, context, _event):
        self.filepath = "arp_bone_hierarchy.json"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        scene = context.scene
        armatures = _validated_armatures(self, scene)
        if armatures is None:
            return {"CANCELLED"}
        source, target = armatures

        filepath = bpy.path.abspath(self.filepath)
        if not filepath.lower().endswith(".json"):
            filepath += ".json"
        payload = {
            "format": "script_toolkit.arp_bone_hierarchy",
            "version": 1,
            "purpose": "Use Source and Target armature hierarchies to create an Auto-Rig Pro .bmap mapping",
            "source_armature": _bone_hierarchy_payload(source),
            "target_armature": _bone_hierarchy_payload(target),
        }

        try:
            parent = os.path.dirname(filepath)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(filepath, "w", encoding="utf-8", newline="\n") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
        except (OSError, TypeError, ValueError, UnicodeError) as error:
            self.report({"ERROR"}, f"Could not write bone hierarchy: {error}")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Exported {len(source.data.bones)} Source and {len(target.data.bones)} Target bones to {filepath}",
        )
        return {"FINISHED"}


def _import_bmap_file(scene, filepath, clear_current=True):
    filepath = bpy.path.abspath(filepath)
    try:
        with open(filepath, "r", encoding="utf-8") as file:
            lines = file.read().splitlines()
    except (OSError, UnicodeError) as error:
        return 0, f"Could not read preset: {error}"

    if len(lines) % 5 != 0:
        return 0, "This does not look like a 5-line Auto-Rig Pro .bmap preset"

    if clear_current:
        scene.arp_retarget_mapping_items.clear()

    by_source = {item.source_name: item for item in scene.arp_retarget_mapping_items}
    imported = 0
    for index in range(0, len(lines), 5):
        first_line, source_name, root, ik, ik_pole = lines[index : index + 5]
        parts = first_line.split("%")
        target_name = parts[0] if parts else ""
        target_armature = scene.arp_retarget_target_armature
        if target_name == "None" and not (target_armature and target_armature.data.bones.get("None")):
            target_name = ""
        item = by_source.get(source_name)
        if item is None:
            item = scene.arp_retarget_mapping_items.add()
            item.source_name = source_name
            by_source[source_name] = item

        item.target_name = target_name
        item.target_manual = True
        if len(parts) >= 9:
            item.location = _parse_bool(parts[1])
            item.ik_auto_pole = parts[2] if parts[2] in {"ABSOLUTE", "RELATIVE_TARGET", "RELATIVE_CHAIN"} else "ABSOLUTE"
            item.rot_add = _parse_vector(parts[3])
            item.loc_add = _parse_vector(parts[4])
            try:
                item.loc_mult = float(parts[5])
            except ValueError:
                item.loc_mult = 1.0
            item.ik_create_constraints = _parse_bool(parts[6])
            item.ik_world = _parse_bool(parts[7])
            if parts[8] in {"X", "Y", "Z", "-X", "-Y", "-Z"}:
                item.ik_axis_correction = parts[8]
        item.set_as_root = _parse_bool(root)
        item.ik = _parse_bool(ik)
        item.ik_pole = ik_pole
        item.selected = False
        imported += 1

    scene.arp_retarget_mapping_index = 0
    scene.arp_retarget_selection_anchor = -1
    return imported, ""


def _on_preset_selection_update(scene, context):
    del context
    if not scene.arp_retarget_preset_selection:
        return
    filepath = _selected_preset_filepath(
        scene,
        scene.arp_retarget_preset_selection,
    )
    if not filepath:
        return
    _import_bmap_file(scene, filepath)


class STARP_OT_refresh_preset_items(Operator):
    bl_idname = "script_toolkit.arp_refresh_preset_items"
    bl_label = "Refresh Mapping Presets"
    bl_description = "Refresh .bmap files from the Auto-Rig Pro Remap Presets folder"
    bl_options = {"INTERNAL"}

    def execute(self, context):
        files = _refresh_preset_items(context.scene)
        self.report({"INFO"}, f"Found {len(files)} .bmap presets")
        return {"FINISHED"}


class STARP_OT_import_bmap(Operator):
    bl_idname = "script_toolkit.arp_import_bmap"
    bl_label = "Import .bmap Preset"
    bl_description = "Load an Auto-Rig Pro .bmap preset into the mapping list"

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.bmap", options={"HIDDEN"})
    clear_current: BoolProperty(name="Replace Current List", default=True)

    def invoke(self, context, _event):
        self.filepath = ""
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        imported, error = _import_bmap_file(
            context.scene,
            self.filepath,
            clear_current=self.clear_current,
        )
        if error:
            self.report({"ERROR"}, error)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {imported} mappings")
        return {"FINISHED"}


def _draw_mapping_options(layout, item, target):
    box = layout.box()
    box.label(text=f"Selected: {item.source_name}", icon="BONE_DATA")
    if target and target.type == "ARMATURE":
        box.prop_search(item, "target_name", target.data, "bones", text="Target Bone")
    else:
        box.prop(item, "target_name", text="Target Bone")
    row = box.row(align=True)
    row.prop(item, "set_as_root")
    row.prop(item, "location")
    row.prop(item, "ik")
    if item.ik:
        row = box.row(align=True)
        row.prop(item, "ik_pole")
        row.prop(item, "ik_world")
        row = box.row(align=True)
        row.prop(item, "ik_auto_pole")
        row.prop(item, "ik_create_constraints")
        box.prop(item, "ik_axis_correction")


def draw_ui(layout, context):
    scene = context.scene
    source = scene.arp_retarget_source_armature
    target = scene.arp_retarget_target_armature
    items = scene.arp_retarget_mapping_items

    presets = layout.box()
    preset_header = presets.row(align=True)
    preset_header.label(text="Mapping Preset", icon="FILE_TEXT")
    preset_header.operator(STARP_OT_refresh_preset_items.bl_idname, text="", icon="FILE_REFRESH")
    row = presets.row(align=True)
    row.prop_search(
        scene,
        "arp_retarget_preset_selection",
        scene,
        "arp_retarget_preset_items",
        text="Preset",
        icon="FILE",
    )
    row = presets.row(align=True)
    row.operator(STARP_OT_import_bmap.bl_idname, text="Import")
    row.operator(STARP_OT_export_bmap.bl_idname, text="Export .bmap")
    presets.operator(STARP_OT_send_to_arp.bl_idname, text="Send to ARP", icon="EXPORT")

    inputs = layout.box()
    inputs_header = inputs.row(align=True)
    inputs_header.label(text="Auto-Rig Pro Remap Preset", icon="ARMATURE_DATA")
    inputs_header.operator(STARP_OT_export_bone_hierarchy.bl_idname, text="", icon="EXPORT")
    row = inputs.row(align=True)
    row.prop(scene, "arp_retarget_source_armature", text="Source Armature")
    pickup = row.operator(STARP_OT_pick_selected_armature.bl_idname, text="", icon="EYEDROPPER")
    pickup.armature_slot = "SOURCE"
    row = inputs.row(align=True)
    row.prop(scene, "arp_retarget_target_armature", text="Target Armature")
    pickup = row.operator(STARP_OT_pick_selected_armature.bl_idname, text="", icon="EYEDROPPER")
    pickup.armature_slot = "TARGET"
    row = inputs.row(align=True)
    row.operator(STARP_OT_build_list.bl_idname, icon="LINENUMBERS_ON")
    row.operator(STARP_OT_update_list.bl_idname, icon="FILE_REFRESH")

    mapping_box = layout.box()
    header = mapping_box.row(align=True)
    source_total = len(source.data.bones) if source and source.type == "ARMATURE" else 0
    target_total = len(target.data.bones) if target and target.type == "ARMATURE" else 0
    mapped_total = sum(bool(item.target_name) for item in items)
    header.label(text=f"Source Bones ({len(items)}/{source_total})")
    header.label(text=f"Target Bones ({mapped_total}/{target_total})")
    list_row = mapping_box.row()
    list_row.template_list(
        STARP_UL_mapping.__name__,
        "",
        scene,
        "arp_retarget_mapping_items",
        scene,
        "arp_retarget_mapping_index",
        rows=14,
    )

    controls = mapping_box.row(align=True)
    controls.operator(STARP_OT_select_all.bl_idname, text="All")
    controls.operator(STARP_OT_select_none.bl_idname, text="None")
    controls.operator(STARP_OT_select_invert.bl_idname, text="Invert")
    controls.operator(STARP_OT_clear_target.bl_idname, icon="X")
    viewport_actions = mapping_box.row(align=True)
    viewport_actions.operator(
        STARP_OT_synchro_select.bl_idname,
        text="Select Viewport Bone in List",
        icon="VIEWZOOM",
    )
    viewport_actions.operator(
        STARP_OT_select_source_bones.bl_idname,
        text="Select Source Bones in Viewport",
        icon="BONE_DATA",
    )
    mapping_box.operator(
        STARP_OT_add_selected_bone_pair.bl_idname,
        text="Add/Update Selected Pair",
        icon="ADD",
    )
    actions = mapping_box.row(align=True)
    actions.operator(STARP_OT_swap_source_target.bl_idname, icon="ARROW_LEFTRIGHT")
    actions.operator(STARP_OT_mirror_bone_list.bl_idname, icon="MOD_MIRROR")
    actions.operator(STARP_OT_match_target_names.bl_idname, icon="BONE_DATA")
    actions.operator(
        STARP_OT_remove_duplicate_targets.bl_idname,
        text="Remove Duplicate Targets",
        icon="X",
    )

    rename_box = layout.box()
    rename_box.label(text="Rename", icon="SORTALPHA")
    row = rename_box.row(align=True)
    row.prop(scene, "arp_retarget_find", text="Find")
    row.prop(scene, "arp_retarget_replace", text="Replace")
    row = rename_box.row(align=True)
    row.prop(scene, "arp_retarget_prefix", text="Prefix")
    row.prop(scene, "arp_retarget_suffix", text="Suffix")
    rename_box.operator(STARP_OT_rename_source_to_target.bl_idname, icon="FONT_DATA")
    rename_box.operator(STARP_OT_rename_target.bl_idname, icon="SORTALPHA")

    if 0 <= scene.arp_retarget_mapping_index < len(items):
        _draw_mapping_options(layout, items[scene.arp_retarget_mapping_index], target)

    if source and target:
        layout.label(text=f"Ready: {source.name} → {target.name}", icon="CHECKMARK")
    elif not items:
        layout.label(text="Choose both armatures, then Build Bone List", icon="INFO")


CLASSES = (
    STARP_PresetItem,
    STARP_MappingItem,
    STARP_OT_select_mapping_row,
    STARP_OT_target_mapping_cell,
    STARP_OT_synchro_select,
    STARP_OT_select_source_bones,
    STARP_OT_add_selected_bone_pair,
    STARP_OT_send_to_arp,
    STARP_OT_pick_selected_armature,
    STARP_UL_mapping,
    STARP_OT_build_list,
    STARP_OT_update_list,
    STARP_OT_match_target_names,
    STARP_OT_remove_duplicate_targets,
    STARP_OT_select_all,
    STARP_OT_select_none,
    STARP_OT_select_invert,
    STARP_OT_clear_target,
    STARP_OT_swap_source_target,
    STARP_OT_mirror_bone_list,
    STARP_OT_rename_source_to_target,
    STARP_OT_rename_target,
    STARP_OT_refresh_preset_items,
    STARP_OT_export_bmap,
    STARP_OT_export_bone_hierarchy,
    STARP_OT_import_bmap,
)


def register():
    _cancel_preset_items_refresh()
    for cls in CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Scene.arp_retarget_source_armature = PointerProperty(
        name="Source Armature", type=bpy.types.Object, poll=_armature_poll
    )
    bpy.types.Scene.arp_retarget_target_armature = PointerProperty(
        name="Target Armature", type=bpy.types.Object, poll=_armature_poll
    )
    bpy.types.Scene.arp_retarget_mapping_items = CollectionProperty(type=STARP_MappingItem)
    bpy.types.Scene.arp_retarget_mapping_index = IntProperty(default=0)
    bpy.types.Scene.arp_retarget_selection_anchor = IntProperty(default=-1)
    bpy.types.Scene.arp_retarget_inline_edit_index = IntProperty(default=-1, options={"HIDDEN"})
    bpy.types.Scene.arp_retarget_find = StringProperty(name="Find", default="")
    bpy.types.Scene.arp_retarget_replace = StringProperty(name="Replace", default="")
    bpy.types.Scene.arp_retarget_prefix = StringProperty(name="Prefix", default="")
    bpy.types.Scene.arp_retarget_suffix = StringProperty(name="Suffix", default="")
    bpy.types.Scene.arp_retarget_preset_items = CollectionProperty(type=STARP_PresetItem)
    bpy.types.Scene.arp_retarget_preset_selection = StringProperty(
        name="Preset",
        default="",
        update=_on_preset_selection_update,
    )
    _schedule_preset_items_refresh()


def unregister():
    _cancel_preset_items_refresh()
    for name in reversed(_SCENE_PROPERTIES):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
