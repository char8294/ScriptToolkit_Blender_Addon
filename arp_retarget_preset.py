"""Build and export a compact Auto-Rig Pro-compatible bone mapping preset."""

import difflib
import os
import re

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
    "arp_retarget_find",
    "arp_retarget_replace",
)

_IK_AXES = (
    ("X", "X", "X"),
    ("Y", "Y", "Y"),
    ("Z", "Z", "Z"),
    ("-X", "-X", "-X"),
    ("-Y", "-Y", "-Y"),
    ("-Z", "-Z", "-Z"),
)


def _armature_poll(_self, obj):
    return obj.type == "ARMATURE"


def _is_arp_armature(obj):
    if not obj or obj.type != "ARMATURE":
        return False
    return bool(obj.data.bones.get("c_traj") and obj.data.bones.get("c_pos"))


def _bone_base_name(name):
    return re.sub(r"\.\d{3}$", "", name)


def _is_excluded_arp_bone(name):
    excluded_prefixes = ("c_p_", "c_foot_bank_")
    excluded_names = {
        "c_foot_fk_scale_fix",
        "c_hand_fk_scale_fix",
        "c_foot_roll",
        "c_foot_heel",
        "c_toes_track",
        "c_toes_end",
        "c_toes_end_01",
        "c_thumb1_rot",
        "c_thumb2_rot",
        "c_thumb3_rot",
        "c_index1_rot",
        "c_index2_rot",
        "c_index3_rot",
        "c_middle1_rot",
        "c_middle2_rot",
        "c_middle3_rot",
        "c_ring1_rot",
        "c_ring2_rot",
        "c_ring3_rot",
        "c_pinky1_rot",
        "c_pinky2_rot",
        "c_pinky3_rot",
    }
    return name.startswith(excluded_prefixes) or _bone_base_name(name) in excluded_names


def _included_bones(obj, is_source=False):
    bones = list(obj.data.bones)
    if not _is_arp_armature(obj):
        return bones

    if is_source:
        return [bone for bone in bones if not _is_excluded_arp_bone(bone.name)]

    result = []
    for bone in bones:
        has_custom_controller = getattr(bone, "get", lambda _key: None)("cc") is not None
        if (bone.name.startswith("c_") or has_custom_controller) and not _is_excluded_arp_bone(bone.name):
            result.append(bone)
    return result


def _tokens(name):
    aliases = {"left": "l", "right": "r", "lft": "l", "rgt": "r"}
    values = []
    for token in re.findall(r"[A-Za-z0-9]+", name.lower()):
        if token == "def":
            continue
        values.append(aliases.get(token, token))
    return values


def _canonical_name(name):
    return " ".join(sorted(_tokens(name)))


def _side(name):
    values = set(_tokens(name))
    if "l" in values and "r" not in values:
        return "l"
    if "r" in values and "l" not in values:
        return "r"
    return None


def _match_score(source_name, target_name):
    source_tokens = _tokens(source_name)
    target_tokens = _tokens(target_name)
    if not source_tokens or not target_tokens:
        return 0.0

    source_side = _side(source_name)
    target_side = _side(target_name)
    if source_side and target_side and source_side != target_side:
        return 0.0

    source_set = set(source_tokens)
    target_set = set(target_tokens)
    common = len(source_set & target_set)
    if common == 0:
        return 0.0

    source_key = _canonical_name(source_name)
    target_key = _canonical_name(target_name)
    if source_key == target_key:
        return 1.0

    # A source bone may carry an import prefix while the target carries an
    # extra rig prefix. Treat either name as a useful subset of the other.
    subset_ratio = common / max(1, min(len(source_set), len(target_set)))
    jaccard = common / max(1, len(source_set | target_set))
    sequence = difflib.SequenceMatcher(None, source_key, target_key).ratio()
    if source_set <= target_set or target_set <= source_set:
        return 0.72 + (subset_ratio * 0.18) + (sequence * 0.10)
    if common < 2:
        return 0.0
    return (jaccard * 0.55) + (sequence * 0.45)


def _find_target(source_name, target_names, assigned):
    candidates = []
    for target_name in target_names:
        if target_name in assigned:
            continue
        score = _match_score(source_name, target_name)
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


class STARP_UL_mapping(UIList):
    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_property, _index):
        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        split = row.split(factor=0.52, align=True)
        split.label(text=item.source_name)
        split.label(text=item.target_name or "None")


class STARP_OT_build_list(Operator):
    bl_idname = "script_toolkit.arp_build_bone_list"
    bl_label = "Build Bone List"
    bl_description = "Create a source-to-target list and guess compatible target names"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        source = scene.arp_retarget_source_armature
        target = scene.arp_retarget_target_armature
        if not source or source.type != "ARMATURE":
            self.report({"ERROR"}, "Choose a Source Armature first")
            return {"CANCELLED"}
        if not target or target.type != "ARMATURE":
            self.report({"ERROR"}, "Choose a Target Armature first")
            return {"CANCELLED"}
        if source == target:
            self.report({"ERROR"}, "Source and Target Armature must be different")
            return {"CANCELLED"}

        source_names = sorted((bone.name for bone in _included_bones(source, is_source=True)), key=str.casefold)
        target_names = sorted((bone.name for bone in _included_bones(target)), key=str.casefold)
        scene.arp_retarget_mapping_items.clear()

        assigned = set()
        matched = 0
        for source_name in source_names:
            item = scene.arp_retarget_mapping_items.add()
            item.source_name = source_name
            item.target_name = _find_target(source_name, target_names, assigned)
            if item.target_name:
                assigned.add(item.target_name)
                matched += 1

        scene.arp_retarget_mapping_index = 0
        self.report({"INFO"}, f"Built {len(source_names)} source bones; matched {matched} target bones")
        return {"FINISHED"}


class STARP_OT_select_all(Operator):
    bl_idname = "script_toolkit.arp_select_all"
    bl_label = "Select All"
    bl_options = {"UNDO"}

    def execute(self, context):
        for item in context.scene.arp_retarget_mapping_items:
            item.selected = True
        return {"FINISHED"}


class STARP_OT_select_none(Operator):
    bl_idname = "script_toolkit.arp_select_none"
    bl_label = "Select None"
    bl_options = {"UNDO"}

    def execute(self, context):
        for item in context.scene.arp_retarget_mapping_items:
            item.selected = False
        return {"FINISHED"}


class STARP_OT_select_invert(Operator):
    bl_idname = "script_toolkit.arp_select_invert"
    bl_label = "Invert Selection"
    bl_options = {"UNDO"}

    def execute(self, context):
        for item in context.scene.arp_retarget_mapping_items:
            item.selected = not item.selected
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
        self.report({"INFO"}, f"Cleared {len(items)} target names")
        return {"FINISHED"}


class STARP_OT_rename_source_to_target(Operator):
    bl_idname = "script_toolkit.arp_rename_source_to_target"
    bl_label = "Rename to Target"
    bl_description = "Replace text in each selected source name and write the result to Target Bone"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        find_text = scene.arp_retarget_find
        replace_text = scene.arp_retarget_replace
        if not find_text:
            self.report({"WARNING"}, "Find cannot be empty")
            return {"CANCELLED"}

        items = _selected_or_active(scene)
        if not items:
            self.report({"WARNING"}, "Select at least one mapping row")
            return {"CANCELLED"}

        changed = 0
        for item in items:
            new_name = item.source_name.replace(find_text, replace_text)
            if new_name != item.target_name:
                item.target_name = new_name
                changed += 1
        self.report({"INFO"}, f"Renamed {changed} target names from source names")
        return {"FINISHED"}


class STARP_OT_rename_target(Operator):
    bl_idname = "script_toolkit.arp_rename_target"
    bl_label = "Rename Target Bone"
    bl_description = "Set the target mapping name for the active row"
    bl_options = {"REGISTER", "UNDO"}

    new_name: StringProperty(name="Target Bone")

    def invoke(self, context, _event):
        scene = context.scene
        if not (0 <= scene.arp_retarget_mapping_index < len(scene.arp_retarget_mapping_items)):
            self.report({"WARNING"}, "Select a mapping row first")
            return {"CANCELLED"}
        self.new_name = scene.arp_retarget_mapping_items[scene.arp_retarget_mapping_index].target_name
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, _context):
        self.layout.prop(self, "new_name", text="Target Bone")

    def execute(self, context):
        scene = context.scene
        if not (0 <= scene.arp_retarget_mapping_index < len(scene.arp_retarget_mapping_items)):
            self.report({"WARNING"}, "Select a mapping row first")
            return {"CANCELLED"}
        scene.arp_retarget_mapping_items[scene.arp_retarget_mapping_index].target_name = self.new_name.strip()
        return {"FINISHED"}


class STARP_OT_export_bmap(Operator):
    bl_idname = "script_toolkit.arp_export_bmap"
    bl_label = "Export .bmap Preset"
    bl_description = "Save the mapping in Auto-Rig Pro's .bmap preset format"

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.bmap", options={"HIDDEN"})

    def invoke(self, context, _event):
        self.filepath = "retarget_mapping.bmap"
        return context.window_manager.fileselect_add(self)

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
        return context.window_manager.fileselect_add(self)

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
        self.report({"INFO"}, f"Imported {imported} mappings")
        return {"FINISHED"}


def _draw_mapping_options(layout, item):
    box = layout.box()
    box.label(text=f"Selected: {item.source_name}", icon="BONE_DATA")
    row = box.row(align=True)
    row.prop(item, "target_name", text="Target Bone")
    row.operator(STARP_OT_rename_target.bl_idname, text="Rename Target")
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

    inputs = layout.box()
    inputs.label(text="Auto-Rig Pro Remap Preset", icon="ARMATURE_DATA")
    inputs.prop(scene, "arp_retarget_source_armature", text="Source Armature")
    inputs.prop(scene, "arp_retarget_target_armature", text="Target Armature")
    inputs.operator(STARP_OT_build_list.bl_idname, icon="LINENUMBERS_ON")

    mapping_box = layout.box()
    header = mapping_box.row(align=True)
    header.label(text="Source Bones")
    header.label(text="Target Bones")
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
    mapping_box.label(text="Checked rows are multi-selected; with none checked, actions use the active row.", icon="INFO")

    rename_box = layout.box()
    rename_box.label(text="Rename Source to Target", icon="SORTALPHA")
    rename_box.prop(scene, "arp_retarget_find", text="Find")
    rename_box.prop(scene, "arp_retarget_replace", text="Replace")
    rename_box.operator(STARP_OT_rename_source_to_target.bl_idname, icon="FONT_DATA")

    items = scene.arp_retarget_mapping_items
    if 0 <= scene.arp_retarget_mapping_index < len(items):
        _draw_mapping_options(layout, items[scene.arp_retarget_mapping_index])

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
    STARP_UL_mapping,
    STARP_OT_build_list,
    STARP_OT_select_all,
    STARP_OT_select_none,
    STARP_OT_select_invert,
    STARP_OT_clear_target,
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
    bpy.types.Scene.arp_retarget_find = StringProperty(name="Find", default="")
    bpy.types.Scene.arp_retarget_replace = StringProperty(name="Replace", default="")


def unregister():
    for name in reversed(_SCENE_PROPERTIES):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
