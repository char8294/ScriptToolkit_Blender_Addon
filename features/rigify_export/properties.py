"""Persistent, per-rig profile data for Rigify Export."""

import bpy
import json
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)


HIERARCHY_ITEMS = (
    ("KEEP_EXISTING", "Keep Existing", "Keep the existing deform-bone ancestry"),
    ("RIGIFY", "Rigify Hierarchy Fix", "Match Game Rig Tools' ORG-to-DEF hierarchy repair"),
    ("FLAT", "Flat Hierarchy", "Make every exported deform bone a root bone"),
)


def _poll_rigify_rig(_self, obj):
    return bool(obj and obj.type == "ARMATURE" and obj.data and obj.data.get("rig_id"))


def _better_fbx_preset_items(self, context):
    from . import core

    return core.better_fbx_preset_items(self, context)


_PROFILE_ENUM_ITEMS = []
_MAIN_PROFILE_ITEMS = []


def _profile_dropdown_items(rig, _context):
    _PROFILE_ENUM_ITEMS.clear()
    for index, profile in enumerate(getattr(rig, "rex_profiles", ())):
        if profile.uid:
            _PROFILE_ENUM_ITEMS.append(
                (profile.uid, profile.name, "Select this export profile", "ARMATURE_DATA", index)
            )
    if not _PROFILE_ENUM_ITEMS:
        _PROFILE_ENUM_ITEMS.append(("NONE", "No Profile", "No export profiles", "INFO", 0))
    return _PROFILE_ENUM_ITEMS


def _update_profile_dropdown(rig, _context):
    uid = rig.rex_profile_dropdown
    for index, profile in enumerate(getattr(rig, "rex_profiles", ())):
        if profile.uid == uid:
            rig.rex_profile_index = index
            break


def _main_profile_items(_scene, _context):
    _MAIN_PROFILE_ITEMS.clear()
    found = set()
    embedded_items = []

    bootstrap = bpy.data.texts.get("REX_Standalone_Exporter.py")
    state_text = bpy.data.texts.get("REX_Standalone_Exporter_State")
    if bootstrap and bootstrap.use_module and state_text:
        try:
            state = json.loads(state_text.as_string())
        except (TypeError, ValueError):
            state = {}
        if state.get("version") == 1:
            for rig_data in state.get("rigs", ()):
                rig_object = bpy.data.objects.get(rig_data.get("name", ""))
                if (
                    not rig_object
                    or rig_object.type != "ARMATURE"
                    or not rig_object.data
                    or not rig_object.data.get("rig_id")
                ):
                    continue
                for profile_data in rig_data.get("profiles", ()):
                    uid = profile_data.get("uid", "")
                    if not uid or any(item[0] == uid for item in embedded_items):
                        continue
                    label = f"{profile_data.get('name', 'Character Export')} — {rig_data.get('name', 'Rig')}"
                    embedded_items.append(
                        (uid, label[:63], "Profile saved in the embedded exporter script", "FILE_SCRIPT", len(_MAIN_PROFILE_ITEMS))
                    )

    for rig in bpy.data.objects:
        if (
            rig.type != "ARMATURE"
            or not rig.data
            or not rig.data.get("rig_id")
            or not hasattr(rig, "rex_profiles")
        ):
            continue
        for profile in rig.rex_profiles:
            if not profile.uid or profile.uid in found:
                continue
            found.add(profile.uid)
            label = f"{profile.name or 'Character Export'} — {rig.name}"
            _MAIN_PROFILE_ITEMS.append(
                (profile.uid, label[:63], "Export profile in this Blender file", "ARMATURE_DATA", len(_MAIN_PROFILE_ITEMS))
            )
    for item in embedded_items:
        if item[0] in found:
            continue
        found.add(item[0])
        _MAIN_PROFILE_ITEMS.append((*item[:4], len(_MAIN_PROFILE_ITEMS)))
    for profile in getattr(_scene, "rex_draft_profiles", ()):
        if not profile.uid or profile.uid in found:
            continue
        found.add(profile.uid)
        label = f"{profile.name or 'Character Export'} — Unassigned"
        _MAIN_PROFILE_ITEMS.append(
            (profile.uid, label[:63], "New profile awaiting a Control Rig", "ARMATURE_DATA", len(_MAIN_PROFILE_ITEMS))
        )
    if not _MAIN_PROFILE_ITEMS:
        _MAIN_PROFILE_ITEMS.append(("NONE", "No Profiles", "Create a profile with New", "INFO", 0))
    return _MAIN_PROFILE_ITEMS


def _update_main_profile_selection(scene, _context):
    uid = scene.rex_main_profile_uid
    if uid in {"NONE", "NEW_PROFILE"}:
        return
    for rig in bpy.data.objects:
        if (
            rig.type != "ARMATURE"
            or not rig.data
            or not rig.data.get("rig_id")
            or not hasattr(rig, "rex_profiles")
        ):
            continue
        for index, profile in enumerate(rig.rex_profiles):
            if profile.uid != uid:
                continue
            if hasattr(scene, "rex_control_rig"):
                scene.rex_control_rig = rig
            rig.rex_profile_index = index
            rig.rex_profile_dropdown = uid
            return

FRAME_RANGE_ITEMS = (
    ("ACTION", "Action", "Bake the full Action frame range"),
    ("SET", "Set", "Bake a specific inclusive frame range"),
    ("TRIM", "Trim", "Trim frames from the start and end of the Action"),
)


def _update_set_start(self, _context):
    if self.set_frame_start > self.set_frame_end:
        self.set_frame_end = self.set_frame_start + 1


def _update_set_end(self, _context):
    if self.set_frame_end < self.set_frame_start:
        self.set_frame_start = self.set_frame_end - 1


def _trim_max(action, other_trim):
    if not action:
        return 0
    start = int(action.frame_range[0])
    end = int(action.frame_range[1])
    return max(0, end - start - 1 - other_trim)


def _update_trim_start(self, _context):
    clamped = min(self.trim_start, _trim_max(self.action, self.trim_end))
    if clamped != self.trim_start:
        self.trim_start = clamped


def _update_trim_end(self, _context):
    clamped = min(self.trim_end, _trim_max(self.action, self.trim_start))
    if clamped != self.trim_end:
        self.trim_end = clamped


class REX_PG_PreviewBone(bpy.types.PropertyGroup):
    name: StringProperty()
    parent_name: StringProperty(name="Parent")
    depth: IntProperty(default=0, min=0)


class REX_PG_ActionItem(bpy.types.PropertyGroup):
    action: PointerProperty(type=bpy.types.Action, name="Action")
    action_slot_identifier: StringProperty(name="Action Slot")
    user_added: BoolProperty(default=False, options={"HIDDEN"})
    expanded: BoolProperty(name="Expanded", default=False, options={"HIDDEN"})
    enabled: BoolProperty(name="Export", default=True)
    use_baked_action_name: BoolProperty(name="Set Baked Action name", default=False)
    baked_action_name: StringProperty(name="Baked Action Name")
    frame_range_mode: EnumProperty(
        name="Frame Range", items=FRAME_RANGE_ITEMS, default="ACTION"
    )
    set_frame_range_initialized: BoolProperty(default=False, options={"HIDDEN"})
    set_frame_start: IntProperty(
        name="Set Start", default=0, min=0, update=_update_set_start
    )
    set_frame_end: IntProperty(
        name="Set End", default=1, min=1, update=_update_set_end
    )
    trim_start: IntProperty(name="Trim Start", default=0, min=0, update=_update_trim_start)
    trim_end: IntProperty(name="Trim End", default=0, min=0, update=_update_trim_end)


class REX_PG_ActionChoice(bpy.types.PropertyGroup):
    action: PointerProperty(type=bpy.types.Action, name="Action")
    selected: BoolProperty(name="Add", default=False)


def _poll_mesh(_self, obj):
    return bool(obj and obj.type == "MESH")


class REX_PG_MeshItem(bpy.types.PropertyGroup):
    mesh: PointerProperty(type=bpy.types.Object, name="Mesh", poll=_poll_mesh)
    enabled: BoolProperty(name="Export", default=True)


def _copy_profile(source, target):
    for name in (
        "uid", "name", "control_rig", "game_rig", "hierarchy_mode",
        "disconnect_bones", "include_mesh", "pack_actions_into_nla",
        "pack_file_name", "mesh_list_initialized",
        "output_directory", "better_fbx_preset",
        "attached", "expanded", "preview_valid",
        "preview_signature", "preview_object", "source_rig",
        "preview_source_was_hidden", "configuration_valid", "source_signature",
        "preview_index", "action_index", "action_to_add", "mesh_index",
    ):
        setattr(target, name, getattr(source, name))

    for source_rows, target_rows, fields in (
        (source.export_bones, target.export_bones, ("name", "parent_name", "depth")),
        (source.preview_bones, target.preview_bones, ("name", "parent_name", "depth")),
        (
            source.action_items,
            target.action_items,
            (
                "action", "action_slot_identifier", "user_added", "expanded", "enabled",
                "use_baked_action_name", "baked_action_name", "frame_range_mode",
                "set_frame_range_initialized", "set_frame_start", "set_frame_end",
                "trim_start", "trim_end",
            ),
        ),
        (source.mesh_items, target.mesh_items, ("mesh", "enabled")),
    ):
        for source_row in source_rows:
            target_row = target_rows.add()
            for name in fields:
                setattr(target_row, name, getattr(source_row, name))


_PROFILE_CONTROL_RIG_UPDATE_GUARD = set()


def _find_profile_container(profile):
    profile_pointer = profile.as_pointer()
    for rig in bpy.data.objects:
        if rig.type != "ARMATURE" or not hasattr(rig, "rex_profiles"):
            continue
        for index, candidate in enumerate(rig.rex_profiles):
            if candidate.as_pointer() == profile_pointer:
                return rig, index, None, -1
    for scene in bpy.data.scenes:
        for index, candidate in enumerate(getattr(scene, "rex_draft_profiles", ())):
            if candidate.as_pointer() == profile_pointer:
                return None, -1, scene, index
    return None, -1, None, -1


def _repair_rig_profile_selection(rig, removed_index, selected_uid):
    if selected_uid:
        selected_index = next(
            (index for index, profile in enumerate(rig.rex_profiles) if profile.uid == selected_uid),
            -1,
        )
    else:
        selected_index = -1
    if selected_index < 0 and rig.rex_profiles:
        selected_index = min(removed_index, len(rig.rex_profiles) - 1)
    if selected_index >= 0:
        rig.rex_profile_index = selected_index
        rig.rex_profile_dropdown = rig.rex_profiles[selected_index].uid
    else:
        rig.rex_profile_index = 0
        rig.rex_profile_dropdown = "NONE"


def _invalidate_profile_configuration(profile):
    profile.configuration_valid = False
    profile.source_signature = ""
    profile.preview_valid = False
    profile.preview_signature = ""
    profile.source_rig = None
    profile.game_rig = None


def _sync_embedded_profiles(scene):
    bootstrap = bpy.data.texts.get("REX_Standalone_Exporter.py")
    if not bootstrap or not bootstrap.use_module:
        return
    from . import standalone

    try:
        standalone.sync_embedded_file(scene)
    except Exception as error:
        print(f"[Rigify Export] Could not sync embedded profiles after Control Rig change: {error}")


def _defer_or_call(cleanup_fn):
    if getattr(bpy.app, "background", False):
        cleanup_fn()
    else:
        bpy.app.timers.register(cleanup_fn, first_interval=0.0)


def _update_profile_control_rig(profile, context):
    guard_key = profile.uid or str(profile.as_pointer())
    if guard_key in _PROFILE_CONTROL_RIG_UPDATE_GUARD:
        return
    _PROFILE_CONTROL_RIG_UPDATE_GUARD.add(guard_key)
    try:
        owner_rig, owner_index, draft_scene, draft_index = _find_profile_container(profile)
        target_rig = profile.control_rig
        if not _poll_rigify_rig(profile, target_rig):
            target_rig = None
            if profile.control_rig is not None:
                profile.control_rig = None

        if profile.preview_object:
            if owner_rig and target_rig != owner_rig:
                profile.control_rig = owner_rig
            return

        if owner_rig == target_rig:
            scene = getattr(context, "scene", None)
            if scene and target_rig:
                scene.rex_control_rig = target_rig
                scene.rex_main_profile_uid = profile.uid
            return

        if target_rig:
            target_profile = target_rig.rex_profiles.add()
            _copy_profile(profile, target_profile)
            _invalidate_profile_configuration(target_profile)
            profile_uid = profile.uid
            if owner_rig:
                selected_uid = owner_rig.rex_profile_dropdown

                def _cleanup_owner():
                    idx = next(
                        (
                            i
                            for i, p in enumerate(getattr(owner_rig, "rex_profiles", ()))
                            if p.uid == profile_uid
                        ),
                        -1,
                    )
                    if idx >= 0:
                        owner_rig.rex_profiles.remove(idx)
                        _repair_rig_profile_selection(owner_rig, idx, selected_uid)
                    return None

                _defer_or_call(_cleanup_owner)
            elif draft_scene:

                def _cleanup_draft():
                    idx = next(
                        (
                            i
                            for i, p in enumerate(
                                getattr(draft_scene, "rex_draft_profiles", ())
                            )
                            if p.uid == profile_uid
                        ),
                        -1,
                    )
                    if idx >= 0:
                        draft_scene.rex_draft_profiles.remove(idx)
                    return None

                _defer_or_call(_cleanup_draft)

            target_rig.rex_profile_index = len(target_rig.rex_profiles) - 1
            target_rig.rex_profile_dropdown = target_profile.uid
            scene = getattr(context, "scene", None)
            if scene:
                scene.rex_control_rig = target_rig
                scene.rex_main_profile_uid = target_profile.uid
                _sync_embedded_profiles(scene)
            return

        if not owner_rig:
            return
        scene = getattr(context, "scene", None)
        if not scene or not hasattr(scene, "rex_draft_profiles"):
            scene = draft_scene
        if not scene:
            profile.control_rig = owner_rig
            return

        selected_uid = owner_rig.rex_profile_dropdown
        draft_profile = scene.rex_draft_profiles.add()
        _copy_profile(profile, draft_profile)
        _invalidate_profile_configuration(draft_profile)
        profile_uid = profile.uid

        def _cleanup_unassigned():
            idx = next(
                (
                    i
                    for i, p in enumerate(getattr(owner_rig, "rex_profiles", ()))
                    if p.uid == profile_uid
                ),
                -1,
            )
            if idx >= 0:
                owner_rig.rex_profiles.remove(idx)
                _repair_rig_profile_selection(owner_rig, idx, selected_uid)
            return None

        _defer_or_call(_cleanup_unassigned)
        if getattr(context, "scene", None):
            context.scene.rex_control_rig = None
            context.scene.rex_main_profile_uid = draft_profile.uid
            _sync_embedded_profiles(context.scene)
    finally:
        _PROFILE_CONTROL_RIG_UPDATE_GUARD.discard(guard_key)


class REX_PG_Profile(bpy.types.PropertyGroup):
    uid: StringProperty()
    name: StringProperty(name="Profile Name", default="Character Export")
    control_rig: PointerProperty(
        type=bpy.types.Object,
        name="Control Rig",
        description="Rigify control armature for this profile; can be changed before a Game Rig preview is generated",
        poll=_poll_rigify_rig,
        update=_update_profile_control_rig,
    )
    game_rig: PointerProperty(
        type=bpy.types.Object,
        name="Game Rig",
        description="Temporary deform-only Game Rig preview generated from the Control Rig",
    )
    hierarchy_mode: EnumProperty(
        name="Hierarchy Settings", items=HIERARCHY_ITEMS, default="RIGIFY"
    )
    disconnect_bones: BoolProperty(name="Disconnect Bones", default=True)
    include_mesh: BoolProperty(name="Include Mesh", default=False)
    pack_actions_into_nla: BoolProperty(
        name="Pack into NLA",
        description="Export all enabled Actions together as separate NLA animation tracks in one FBX",
        default=False,
    )
    pack_file_name: StringProperty(
        name="File Name",
        description="Base name for the packed FBX; the .fbx extension is added automatically",
        default="",
    )
    mesh_list_initialized: BoolProperty(default=False, options={"HIDDEN"})
    output_directory: StringProperty(
        name="Output Folder", subtype="DIR_PATH", default=""
    )
    better_fbx_preset: EnumProperty(
        name="Preset",
        description="Choose a Better FBX export preset",
        items=_better_fbx_preset_items,
    )
    attached: BoolProperty(name="Show under Rig Layers", default=False)
    expanded: BoolProperty(name="Expanded", default=True)
    preview_valid: BoolProperty(default=False)
    preview_signature: StringProperty()
    preview_object: PointerProperty(type=bpy.types.Object, name="Rig Preview")
    source_rig: PointerProperty(type=bpy.types.Object, name="Source Rigify Rig")
    preview_source_was_hidden: BoolProperty(default=False, options={"HIDDEN"})
    configuration_valid: BoolProperty(default=False)
    source_signature: StringProperty()
    export_bones: CollectionProperty(type=REX_PG_PreviewBone)
    preview_bones: CollectionProperty(type=REX_PG_PreviewBone)
    preview_index: IntProperty(default=0)
    action_items: CollectionProperty(type=REX_PG_ActionItem)
    action_index: IntProperty(default=0)
    action_to_add: PointerProperty(type=bpy.types.Action, name="Add Action")
    mesh_items: CollectionProperty(type=REX_PG_MeshItem)
    mesh_index: IntProperty(default=0)


class REX_PG_ProfileIndex(bpy.types.PropertyGroup):
    pass


CLASSES = (
    REX_PG_PreviewBone,
    REX_PG_ActionItem,
    REX_PG_ActionChoice,
    REX_PG_MeshItem,
    REX_PG_Profile,
    REX_PG_ProfileIndex,
)


def _register_property(owner, name, make_definition):
    """Register an RNA property only once, preserving data on a repeated enable."""
    if not hasattr(owner, name):
        setattr(owner, name, make_definition())


def _registered_class(cls):
    """Find a registered RNA class even when bpy.types has no Python attribute for it."""
    for base in cls.__mro__[1:]:
        lookup = getattr(base, "bl_rna_get_subclass_py", None)
        if lookup is None:
            continue
        try:
            registered_cls = lookup(cls.__name__, None)
        except (AttributeError, TypeError):
            continue
        if registered_cls is not None:
            return registered_cls
    return None


def register():
    # Blender can call register again after a script reload while the existing
    # PropertyGroup classes and Object/Scene data are still live. Re-registering
    # those classes raises "already registered as a subclass" and replacing
    # their CollectionProperties discards the current profiles.
    registered_types = {}
    for cls in CLASSES:
        registered_cls = _registered_class(cls)
        if registered_cls is None:
            bpy.utils.register_class(cls)
            registered_cls = cls
        registered_types[cls.__name__] = registered_cls

    profile_type = registered_types["REX_PG_Profile"]
    action_choice_type = registered_types["REX_PG_ActionChoice"]
    _register_property(
        bpy.types.Object,
        "rex_profiles",
        lambda: CollectionProperty(type=profile_type),
    )
    _register_property(
        bpy.types.Object, "rex_profile_index", lambda: IntProperty(default=0)
    )
    _register_property(
        bpy.types.Object,
        "rex_profile_dropdown",
        lambda: EnumProperty(
            name="Export Profile",
            description="Choose an export profile",
            items=_profile_dropdown_items,
            update=_update_profile_dropdown,
        ),
    )
    _register_property(
        bpy.types.Scene,
        "rex_control_rig",
        lambda: PointerProperty(
            type=bpy.types.Object,
            name="Control Rig",
            description="Rigify armature used as the source for Game Rig export profiles",
            poll=_poll_rigify_rig,
        ),
    )
    _register_property(
        bpy.types.Scene,
        "rex_main_profile_uid",
        lambda: EnumProperty(
            name="Export Profile",
            description="Choose an export profile",
            items=_main_profile_items,
            update=_update_main_profile_selection,
        ),
    )
    _register_property(
        bpy.types.Scene,
        "rex_draft_profiles",
        lambda: CollectionProperty(type=profile_type),
    )
    _register_property(
        bpy.types.WindowManager,
        "rex_action_picker_items",
        lambda: CollectionProperty(type=action_choice_type),
    )
    _register_property(
        bpy.types.WindowManager,
        "rex_action_picker_index",
        lambda: IntProperty(default=0),
    )
    _register_property(
        bpy.types.WindowManager,
        "rex_action_picker_search",
        lambda: StringProperty(
            name="Search Actions",
            default="",
            options={"TEXTEDIT_UPDATE"},
        ),
    )


def unregister():
    if hasattr(bpy.types.WindowManager, "rex_action_picker_search"):
        del bpy.types.WindowManager.rex_action_picker_search
    if hasattr(bpy.types.WindowManager, "rex_action_picker_index"):
        del bpy.types.WindowManager.rex_action_picker_index
    if hasattr(bpy.types.WindowManager, "rex_action_picker_items"):
        del bpy.types.WindowManager.rex_action_picker_items
    if hasattr(bpy.types.Scene, "rex_draft_profiles"):
        del bpy.types.Scene.rex_draft_profiles
    if hasattr(bpy.types.Scene, "rex_main_profile_uid"):
        del bpy.types.Scene.rex_main_profile_uid
    if hasattr(bpy.types.Scene, "rex_control_rig"):
        del bpy.types.Scene.rex_control_rig
    if hasattr(bpy.types.Object, "rex_profile_index"):
        del bpy.types.Object.rex_profile_index
    if hasattr(bpy.types.Object, "rex_profile_dropdown"):
        del bpy.types.Object.rex_profile_dropdown
    if hasattr(bpy.types.Object, "rex_profiles"):
        del bpy.types.Object.rex_profiles
    for cls in reversed(CLASSES):
        registered_cls = _registered_class(cls)
        if registered_cls is None:
            continue
        try:
            bpy.utils.unregister_class(registered_cls)
        except RuntimeError:
            pass
