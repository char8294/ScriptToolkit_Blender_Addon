"""Build and export a compact Auto-Rig Pro-compatible bone mapping preset."""

import difflib
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
from bpy.types import Operator, PropertyGroup, UIList


_SCENE_PROPERTIES = (
    "arp_retarget_source_armature",
    "arp_retarget_target_armature",
    "arp_retarget_mapping_items",
    "arp_retarget_mapping_index",
    "arp_retarget_selection_anchor",
    "arp_retarget_find",
    "arp_retarget_replace",
    "arp_retarget_prefix",
    "arp_retarget_suffix",
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


def _left_aligned_operator_text(context, text):
    """Pad an operator label so Blender's centered text appears left-aligned."""
    text = text or "None"
    if context is None or context.region is None:
        return text

    ui_scale = context.preferences.system.ui_scale
    blf.size(0, max(1, round(_LIST_FONT_SIZE * ui_scale)))
    cell_width = max(
        80.0,
        (context.region.width - (_LIST_REGION_SIDE_PADDING * ui_scale)) * 0.5,
    )
    text_width = blf.dimensions(0, text)[0]
    space_width = max(1.0, blf.dimensions(0, _LIST_LABEL_SPACE)[0])
    padding_width = max(
        0.0,
        cell_width - text_width - (_LIST_LABEL_END_MARGIN * ui_scale),
    )
    return text + (_LIST_LABEL_SPACE * int(padding_width / space_width))

_TARGET_DOUBLE_CLICK_SECONDS = 0.4
_last_target_click_index = -1
_last_target_click_time = 0.0

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


def _find_target(source_signature, target_signatures, assigned):
    candidates = []
    for target_name, target_signature in target_signatures.items():
        if target_name in assigned:
            continue
        score = _match_score(source_signature, target_signature)
        if score >= 0.52:
            candidates.append((score, target_name))
    if not candidates:
        return ""
    candidates.sort(key=lambda value: (-value[0], value[1].casefold()))
    return candidates[0][1]


def _selected_or_active(scene):
    items = scene.arp_retarget_mapping_items
    selected = [item for item in items if item.selected]
    if selected:
        return selected
    if 0 <= scene.arp_retarget_mapping_index < len(items):
        return [items[scene.arp_retarget_mapping_index]]
    return []


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
    if is_double:
        _reset_target_click_state()
    elif has_modifier:
        _reset_target_click_state()
    else:
        _last_target_click_index = index
        _last_target_click_time = current_time
    return is_double


def _select_mapping_row(scene, index, select_range=False, extend=False, deselect=False):
    items = scene.arp_retarget_mapping_items
    if not (0 <= index < len(items)):
        return False

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


class STARP_MappingItem(PropertyGroup):
    source_name: StringProperty(name="Source Bone")
    target_name: StringProperty(name="Target Bone", default="")
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
    bl_description = "Click selects one; Shift selects a range; Ctrl adds; Alt removes; double-click edits Target Bone"
    bl_options = {"INTERNAL", "UNDO"}

    index: IntProperty()
    new_name: StringProperty(name="Target Bone")
    editing: BoolProperty(default=False, options={"HIDDEN"})

    def invoke(self, context, event):
        scene = context.scene
        items = scene.arp_retarget_mapping_items
        if not (0 <= self.index < len(items)):
            return {"CANCELLED"}

        if _is_target_double_click(self.index, event):
            scene.arp_retarget_mapping_index = self.index
            self.editing = True
            self.new_name = items[self.index].target_name
            return context.window_manager.invoke_props_dialog(self, width=520)

        self.editing = False
        if not _select_mapping_row(
            scene,
            self.index,
            select_range=event.shift,
            extend=event.ctrl,
            deselect=event.alt,
        ):
            return {"CANCELLED"}
        return {"FINISHED"}

    def draw(self, _context):
        self.layout.prop(self, "new_name", text="Target Bone")

    def execute(self, context):
        scene = context.scene
        if not (0 <= self.index < len(scene.arp_retarget_mapping_items)):
            return {"CANCELLED"}
        if self.editing:
            scene.arp_retarget_mapping_items[self.index].target_name = self.new_name.strip()
            scene.arp_retarget_mapping_items[self.index].target_manual = True
        else:
            _select_mapping_row(scene, self.index)
        return {"FINISHED"}


class STARP_UL_mapping(UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_property, _index):
        split = layout.split(factor=0.5, align=True)
        source = split.operator(
            STARP_OT_select_mapping_row.bl_idname,
            text=_left_aligned_operator_text(_context, item.source_name),
            emboss=item.selected,
            depress=item.selected,
        )
        source.index = _index
        target = split.operator(
            STARP_OT_target_mapping_cell.bl_idname,
            text=_left_aligned_operator_text(_context, item.target_name or "None"),
            emboss=item.selected,
            depress=item.selected,
        )
        target.index = _index


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
        filepath = bpy.path.abspath(self.filepath)
        try:
            with open(filepath, "r", encoding="utf-8") as file:
                lines = file.read().splitlines()
        except (OSError, UnicodeError) as error:
            self.report({"ERROR"}, f"Could not read preset: {error}")
            return {"CANCELLED"}

        if len(lines) % 5 != 0:
            self.report({"ERROR"}, "This does not look like a 5-line Auto-Rig Pro .bmap preset")
            return {"CANCELLED"}

        scene = context.scene
        if self.clear_current:
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

    inputs = layout.box()
    inputs.label(text="Auto-Rig Pro Remap Preset", icon="ARMATURE_DATA")
    inputs.prop(scene, "arp_retarget_source_armature", text="Source Armature")
    inputs.prop(scene, "arp_retarget_target_armature", text="Target Armature")
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
    actions = mapping_box.row(align=True)
    actions.operator(STARP_OT_swap_source_target.bl_idname, icon="ARROW_LEFTRIGHT")
    actions.operator(STARP_OT_mirror_bone_list.bl_idname, icon="MOD_MIRROR")

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

    presets = layout.box()
    presets.label(text="Mapping Preset")
    row = presets.row(align=True)
    row.operator(STARP_OT_import_bmap.bl_idname, text="Import")
    row.operator(STARP_OT_export_bmap.bl_idname, text="Export .bmap")

    if source and target:
        layout.label(text=f"Ready: {source.name} → {target.name}", icon="CHECKMARK")
    elif not items:
        layout.label(text="Choose both armatures, then Build Bone List", icon="INFO")


CLASSES = (
    STARP_MappingItem,
    STARP_OT_select_mapping_row,
    STARP_OT_target_mapping_cell,
    STARP_UL_mapping,
    STARP_OT_build_list,
    STARP_OT_update_list,
    STARP_OT_select_all,
    STARP_OT_select_none,
    STARP_OT_select_invert,
    STARP_OT_clear_target,
    STARP_OT_swap_source_target,
    STARP_OT_mirror_bone_list,
    STARP_OT_rename_source_to_target,
    STARP_OT_rename_target,
    STARP_OT_export_bmap,
    STARP_OT_import_bmap,
)


def register():
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
    bpy.types.Scene.arp_retarget_find = StringProperty(name="Find", default="")
    bpy.types.Scene.arp_retarget_replace = StringProperty(name="Replace", default="")
    bpy.types.Scene.arp_retarget_prefix = StringProperty(name="Prefix", default="")
    bpy.types.Scene.arp_retarget_suffix = StringProperty(name="Suffix", default="")


def unregister():
    for name in reversed(_SCENE_PROPERTIES):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
