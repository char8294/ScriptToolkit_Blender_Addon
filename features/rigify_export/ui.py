"""Blender panels and operators for Rigify Export."""

import traceback
import uuid

import bpy
from bpy.app.handlers import persistent
from bpy.props import IntProperty, StringProperty

from . import core
from . import standalone


_panel_classes = []


def _cleanup_empty_preview_collection():
    core.cleanup_empty_preview_collection()
    return None


@persistent
def _schedule_preview_collection_cleanup(_scene, _depsgraph):
    collection = bpy.data.collections.get(core.PREVIEW_COLLECTION_NAME)
    if not collection or len(collection.objects) > 0:
        return
    if not bpy.app.timers.is_registered(_cleanup_empty_preview_collection):
        bpy.app.timers.register(_cleanup_empty_preview_collection, first_interval=0.0)


def _profile_by_uid(rig, uid):
    if not rig or not hasattr(rig, "rex_profiles"):
        return None
    return next((profile for profile in rig.rex_profiles if profile.uid == uid), None)


def _selected_profile(rig):
    if not rig or not hasattr(rig, "rex_profiles"):
        return None
    profile = _profile_by_uid(rig, getattr(rig, "rex_profile_dropdown", ""))
    if profile:
        return profile
    index = rig.rex_profile_index
    return rig.rex_profiles[index] if 0 <= index < len(rig.rex_profiles) else None


def _profile_for_uid(uid):
    if not uid or uid == "NEW_PROFILE":
        return None, None
    for rig in bpy.data.objects:
        if not core.is_rigify_rig(rig) or not hasattr(rig, "rex_profiles"):
            continue
        profile = _profile_by_uid(rig, uid)
        if profile:
            return rig, profile
    for scene in bpy.data.scenes:
        profile = next(
            (candidate for candidate in getattr(scene, "rex_draft_profiles", ()) if candidate.uid == uid),
            None,
        )
        if profile:
            return None, profile
    return None, None


def _control_rig_for_context(context):
    rig = core.source_rig_for_object(getattr(context, "object", None))
    if rig:
        return rig
    scene = getattr(context, "scene", None)
    if scene and hasattr(scene, "rex_control_rig"):
        rig = scene.rex_control_rig
        return rig if core.is_rigify_rig(rig) else None
    return None


def _migrate_legacy_action_items():
    """Restore Action Bakery, preview, and mesh-list data from older profiles."""
    for obj in bpy.data.objects:
        if not core.is_rigify_rig(obj) or not hasattr(obj, "rex_profiles"):
            continue
        for profile in obj.rex_profiles:
            if not profile.uid:
                profile.uid = uuid.uuid4().hex
            profile.attached = False
            if not profile.control_rig:
                profile.control_rig = obj
            if not profile.pack_file_name:
                profile.pack_file_name = core.default_pack_file_name(profile.name)
            for item in profile.action_items:
                core.initialize_action_item_frame_range(item)
                if item.enabled:
                    item.user_added = True
            if profile.preview_object and profile.preview_object.name not in bpy.data.objects:
                profile.preview_object = None
                profile.preview_valid = False
            if profile.preview_object:
                profile.game_rig = profile.preview_object
                core.make_preview_visible(profile.preview_object)
                core.ensure_preview_constraints(
                    profile.preview_object, profile.source_rig or obj
                )
            elif profile.game_rig:
                profile.game_rig = None
            if not profile.mesh_list_initialized:
                if profile.include_mesh:
                    for mesh in core.associated_meshes(bpy.context.scene, obj):
                        if not any(item.mesh == mesh for item in profile.mesh_items):
                            profile.mesh_items.add().mesh = mesh
                profile.mesh_list_initialized = True
        if obj.rex_profiles:
            selected_index = min(max(0, obj.rex_profile_index), len(obj.rex_profiles) - 1)
            if not _profile_by_uid(obj, getattr(obj, "rex_profile_dropdown", "")):
                obj.rex_profile_dropdown = obj.rex_profiles[selected_index].uid


def _action_choice_matches(action, query):
    query = (query or "").strip().casefold()
    return bool(action and (not query or query in action.name.casefold()))


def _populate_action_picker(context, rig, profile):
    choices = context.window_manager.rex_action_picker_items
    choices.clear()
    context.window_manager.rex_action_picker_search = ""
    existing = {
        item.action.as_pointer()
        for item in profile.action_items
        if item.user_added and item.action
    }
    for action in bpy.data.actions:
        if action.as_pointer() in existing:
            continue
        if not getattr(action, "is_action_legacy", False):
            target_types = {"OBJECT"}
            if core.profile_has_shape_keys(profile):
                target_types.add("KEY")
            if not any(slot.target_id_type in target_types for slot in action.slots):
                continue
        choice = choices.add()
        choice.action = action
    context.window_manager.rex_action_picker_index = 0
    return len(choices)


def _draw_action_bakery(layout, profile):
    header, body = layout.panel(
        "REX_ACTION_BAKERY_" + profile.uid,
        default_closed=False,
    )
    header.label(text="Action Bakery", icon="ACTION")
    if body is None:
        return

    visible = [index for index, item in enumerate(profile.action_items) if item.user_added]
    list_row = body.row(align=True)
    list_col = list_row.column()
    list_col.template_list(
        "REX_UL_ActionItems",
        "action_bakery_" + profile.uid,
        profile,
        "action_items",
        profile,
        "action_index",
        rows=min(6, max(4, len(visible))),
    )
    controls = list_row.column(align=True)
    add = controls.operator("rex.add_actions", text="", icon="ADD")
    add.profile_uid = profile.uid
    remove_row = controls.row()
    remove_row.enabled = bool(visible)
    remove = remove_row.operator("rex.remove_action_item", text="", icon="REMOVE")
    remove.profile_uid = profile.uid
    controls.separator()
    up_row = controls.row()
    up_row.enabled = bool(visible)
    up = up_row.operator("rex.move_action_item", text="", icon="TRIA_UP")
    up.profile_uid = profile.uid
    up.direction = -1
    down_row = controls.row()
    down_row.enabled = bool(visible)
    down = down_row.operator("rex.move_action_item", text="", icon="TRIA_DOWN")
    down.profile_uid = profile.uid
    down.direction = 1

    if not visible:
        return

    index = profile.action_index
    if index not in visible:
        body.label(text="Select an Action in the list to edit its settings.", icon="INFO")
        return
    item = profile.action_items[index]
    if not item.user_added or not item.action:
        body.label(text="Choose an Action for this row above.", icon="INFO")
        return

    settings = body.box()
    settings.label(text="Local Settings")
    settings.prop(item, "use_baked_action_name", text="Set Baked Action name")
    if item.use_baked_action_name:
        settings.prop(item, "baked_action_name", text="")

    settings.separator()
    settings.label(text="Frame Range")
    settings.row(align=True).prop(item, "frame_range_mode", expand=True)
    if item.frame_range_mode == "ACTION":
        settings.label(
            text=f"Start: {int(item.action.frame_range[0])}  End: {int(item.action.frame_range[1])}",
            icon="ACTION",
        )
    elif item.frame_range_mode == "SET":
        row = settings.row(align=True)
        row.prop(item, "set_frame_start", text="Set Start")
        row.prop(item, "set_frame_end", text="Set End")
        reset = row.operator("rex.set_action_range", text="", icon="FILE_REFRESH")
        reset.profile_uid = profile.uid
        reset.index = index
    else:
        settings.row(align=True).prop(item, "trim_start", text="Trim Start")
        settings.row(align=True).prop(item, "trim_end", text="Trim End")
        start = int(item.action.frame_range[0]) + item.trim_start
        end = int(item.action.frame_range[1]) - item.trim_end
        settings.label(text=f"Start: {start}  End: {end}", icon="TIME")


def _draw_mesh_list(layout, profile):
    layout.label(text="Meshes to Export", icon="OUTLINER_OB_MESH")
    row = layout.row()
    row.template_list(
        "REX_UL_MeshItems",
        "mesh_items_" + profile.uid,
        profile,
        "mesh_items",
        profile,
        "mesh_index",
        rows=min(5, max(2, len(profile.mesh_items))),
    )
    controls = row.column(align=True)
    add_selected = controls.operator("rex.add_selected_meshes", text="", icon="ADD")
    add_selected.profile_uid = profile.uid
    add_bound = controls.operator("rex.add_bound_meshes", text="", icon="ARMATURE_DATA")
    add_bound.profile_uid = profile.uid
    controls.separator()
    remove = controls.operator("rex.remove_mesh_item", text="", icon="REMOVE")
    remove.profile_uid = profile.uid
    if not profile.mesh_items:
        layout.label(text="Select meshes and use +, or add all meshes bound to this rig.", icon="INFO")


def _configuration_is_current(rig, profile):
    return bool(
        profile.configuration_valid
        and profile.export_bones
        and profile.source_signature
        == core.hierarchy_signature(rig, profile.hierarchy_mode, profile.disconnect_bones)
    )


def _control_rig_editable(profile):
    """Allow correcting the Control Rig until a preview is currently present."""
    return not profile.preview_object


def _registered_class(cls):
    """Resolve a registered Blender class by RNA name, including old module copies."""
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


@persistent
def _load_post(_unused):
    standalone.restore_embedded_state()
    _migrate_legacy_action_items()
    scene = bpy.context.scene
    if scene and not core.is_rigify_rig(getattr(scene, "rex_control_rig", None)):
        active_rig = core.source_rig_for_object(bpy.context.object)
        if active_rig:
            scene.rex_control_rig = active_rig
    for obj in bpy.data.objects:
        if not core.is_rigify_rig(obj):
            continue
        for profile in obj.rex_profiles:
            preview = profile.preview_object
            if preview and preview.get("rex_preview_profile_uid") != profile.uid:
                core.remove_preview_object(profile)
                profile.preview_valid = False
            elif not preview and profile.source_rig:
                core.remove_preview_object(profile)
            elif preview:
                core.make_preview_visible(preview)

    if scene:
        entries = standalone.embedded_profile_entries()
        valid_uids = {entry["uid"] for entry in entries}
        valid_uids.update(
            profile.uid
            for obj in bpy.data.objects
            if core.is_rigify_rig(obj) and hasattr(obj, "rex_profiles")
            for profile in obj.rex_profiles
            if profile.uid
        )
        valid_uids.update(
            profile.uid for profile in scene.rex_draft_profiles if profile.uid
        )
        selected_uid = getattr(scene, "rex_main_profile_uid", "NONE")
        if selected_uid not in valid_uids:
            if entries:
                selected_uid = entries[0]["uid"]
            else:
                selected_rig = getattr(scene, "rex_control_rig", None)
                selected_profile = _selected_profile(selected_rig)
                if selected_profile:
                    selected_uid = selected_profile.uid
                else:
                    selected_uid = next(
                        (
                            profile.uid
                            for obj in bpy.data.objects
                            if core.is_rigify_rig(obj) and hasattr(obj, "rex_profiles")
                            for profile in obj.rex_profiles
                        ),
                        "NONE",
                    )
        scene.rex_main_profile_uid = selected_uid
        selected_rig, selected_profile = _profile_for_uid(selected_uid)
        if selected_rig and selected_profile:
            scene.rex_control_rig = selected_rig
            selected_rig.rex_profile_dropdown = selected_profile.uid
            selected_rig.rex_profile_index = next(
                index
                for index, profile in enumerate(selected_rig.rex_profiles)
                if profile.uid == selected_profile.uid
            )
    core.cleanup_empty_preview_collection()


def _initialize_after_register():
    # addon_utils.register() runs with restricted bpy.data access. Wait until
    # Blender has restored the normal data API before reading embedded profiles.
    if not hasattr(bpy.data, "texts"):
        return 0.1
    _load_post(None)
    return None


class REX_OT_new_profile(bpy.types.Operator):
    bl_idname = "rex.new_profile"
    bl_label = "New"
    bl_description = "Create a new export profile"
    bl_options = {"UNDO"}

    profile_name: StringProperty(name="Profile Name", default="")

    def draw(self, context):
        self.layout.prop(self, "profile_name", text="Name")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        profile_name = self.profile_name.strip()
        if not profile_name:
            self.report({"ERROR"}, "Enter a profile name first.")
            return {"CANCELLED"}
        scene = context.scene
        if any(profile.name == profile_name for profile in scene.rex_draft_profiles):
            self.report({"ERROR"}, "A draft profile already has that name.")
            return {"CANCELLED"}
        profile = scene.rex_draft_profiles.add()
        profile.uid = uuid.uuid4().hex
        profile.name = profile_name
        profile.pack_file_name = core.default_pack_file_name(profile_name)
        profile.mesh_list_initialized = True
        scene.rex_main_profile_uid = profile.uid
        self.report({"INFO"}, f"Created profile '{profile_name}'. Set its Control Rig to continue.")
        return {"FINISHED"}


class REX_OT_delete_profile(bpy.types.Operator):
    bl_idname = "rex.delete_profile"
    bl_label = "Delete Export Profile"
    bl_description = "Delete the selected export profile and remove its embedded entry"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()

    @classmethod
    def poll(cls, context):
        return _profile_for_uid(getattr(context.scene, "rex_main_profile_uid", ""))[1] is not None

    def invoke(self, context, event):
        rig, profile = _profile_for_uid(self.profile_uid)
        if not profile:
            return {"CANCELLED"}
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        rig, profile = _profile_for_uid(self.profile_uid)
        if not profile:
            self.report({"ERROR"}, "Export profile not found.")
            return {"CANCELLED"}

        profile_name = profile.name
        core.remove_preview_object(profile)
        if rig:
            index = next(
                (i for i, candidate in enumerate(rig.rex_profiles) if candidate.uid == profile.uid),
                -1,
            )
            if index < 0:
                return {"CANCELLED"}
            rig.rex_profiles.remove(index)
            rig.rex_profile_index = min(max(0, index), max(0, len(rig.rex_profiles) - 1))
            rig.rex_profile_dropdown = (
                rig.rex_profiles[rig.rex_profile_index].uid if rig.rex_profiles else "NONE"
            )
            try:
                standalone.sync_embedded_file(context.scene)
            except Exception as error:
                self.report({"ERROR"}, f"Could not update embedded profiles: {error}")
                return {"CANCELLED"}
        else:
            index = next(
                (i for i, candidate in enumerate(context.scene.rex_draft_profiles) if candidate.uid == profile.uid),
                -1,
            )
            if index < 0:
                return {"CANCELLED"}
            context.scene.rex_draft_profiles.remove(index)

        entries = standalone.embedded_profile_entries()
        next_uid = entries[0]["uid"] if entries else next(
            (
                candidate.uid
                for obj in bpy.data.objects
                if core.is_rigify_rig(obj) and hasattr(obj, "rex_profiles")
                for candidate in obj.rex_profiles
            ),
            next((candidate.uid for candidate in context.scene.rex_draft_profiles), "NONE"),
        )
        context.scene.rex_main_profile_uid = next_uid
        self.report({"INFO"}, f"Deleted export profile '{profile_name}'.")
        return {"FINISHED"}


class REX_OT_refresh_profiles(bpy.types.Operator):
    bl_idname = "rex.refresh_profiles"
    bl_label = "Refresh Export Profiles"
    bl_description = "Reload profiles stored in the embedded exporter script"

    def execute(self, context):
        standalone.restore_embedded_state()
        _migrate_legacy_action_items()
        entries = standalone.embedded_profile_entries()
        available_uids = {entry["uid"] for entry in entries}
        available_uids.update(
            profile.uid
            for rig in bpy.data.objects
            if core.is_rigify_rig(rig) and hasattr(rig, "rex_profiles")
            for profile in rig.rex_profiles
            if profile.uid
        )
        available_uids.update(
            profile.uid for profile in context.scene.rex_draft_profiles if profile.uid
        )

        selected_uid = context.scene.rex_main_profile_uid
        if selected_uid not in available_uids:
            selected_uid = entries[0]["uid"] if entries else next(
                (
                    profile.uid
                    for rig in bpy.data.objects
                    if core.is_rigify_rig(rig) and hasattr(rig, "rex_profiles")
                    for profile in rig.rex_profiles
                ),
                "NONE",
            )

        context.scene.rex_main_profile_uid = selected_uid
        selected_rig, selected_profile = _profile_for_uid(selected_uid)
        if selected_rig and selected_profile:
            context.scene.rex_control_rig = selected_rig
            selected_rig.rex_profile_dropdown = selected_profile.uid
        self.report(
            {"INFO"},
            f"Found {len(entries)} embedded script profile(s); {len(available_uids)} profile(s) available.",
        )
        return {"FINISHED"}


class REX_OT_generate_preview(bpy.types.Operator):
    bl_idname = "rex.generate_preview"
    bl_label = "Gen Game Rig"
    bl_description = "Generate or regenerate a visible deform-only copy; edit it before embedding the export profile"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()

    @classmethod
    def poll(cls, context):
        rig, profile = _profile_for_uid(
            getattr(context.scene, "rex_main_profile_uid", "")
        )
        return bool(profile and (rig or core.is_rigify_rig(profile.control_rig)))

    def execute(self, context):
        rig, profile = _profile_for_uid(self.profile_uid)
        rig = rig or (profile.control_rig if profile else None)
        if not profile or not core.is_rigify_rig(rig):
            self.report({"ERROR"}, "Export profile not found.")
            return {"CANCELLED"}
        profile = _profile_by_uid(rig, self.profile_uid)
        if not profile:
            self.report({"ERROR"}, "Assign the Control Rig to this profile first.")
            return {"CANCELLED"}
        try:
            preview = core.create_preview_rig(context, rig, profile)
        except Exception as error:
            profile.preview_valid = False
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Generated Game Rig with {len(preview.data.bones)} bones. Review it and delete unwanted DEF bones.",
        )
        return {"FINISHED"}


class REX_OT_focus_preview(bpy.types.Operator):
    bl_idname = "rex.focus_preview"
    bl_label = "Select Game Rig"
    bl_description = "Select the Game Rig and enter Edit Mode to edit its bones"

    profile_uid: StringProperty()

    @classmethod
    def poll(cls, context):
        return _control_rig_for_context(context) is not None

    def execute(self, context):
        rig, profile = _profile_for_uid(self.profile_uid)
        if not profile or not profile.preview_object:
            self.report({"ERROR"}, "Gen Game Rig first.")
            return {"CANCELLED"}
        preview = profile.preview_object
        active = context.view_layer.objects.active
        if active and active.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        core.make_preview_visible(preview)
        core.ensure_preview_constraints(preview, profile.source_rig or rig)
        bpy.ops.object.select_all(action="DESELECT")
        preview.select_set(True)
        context.view_layer.objects.active = preview
        bpy.ops.object.mode_set(mode="EDIT")
        return {"FINISHED"}


class REX_OT_save_profile(bpy.types.Operator):
    bl_idname = "rex.save_profile"
    bl_label = "Save to UI"
    bl_description = "Save the remaining Game Rig bones and finish this profile in the Item tab"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()

    @classmethod
    def poll(cls, context):
        return _control_rig_for_context(context) is not None

    def execute(self, context):
        source_object = context.object
        rig, profile = _profile_for_uid(self.profile_uid)
        if not profile:
            self.report({"ERROR"}, "Export profile not found.")
            return {"CANCELLED"}

        current_signature = core.hierarchy_signature(
            rig, profile.hierarchy_mode, profile.disconnect_bones
        )
        preview = profile.preview_object
        if preview:
            if preview.get("rex_preview_profile_uid") != profile.uid:
                self.report({"ERROR"}, "The preview belongs to a different export profile.")
                return {"CANCELLED"}
            if not profile.preview_valid or profile.preview_signature != current_signature:
                profile.preview_valid = False
                self.report({"ERROR"}, "Rigify settings changed. Generate a fresh preview first.")
                return {"CANCELLED"}
            if preview.mode != "OBJECT":
                context.view_layer.objects.active = preview
                bpy.ops.object.mode_set(mode="OBJECT")
            rows = core.export_bones_from_preview(preview)
            if not rows:
                self.report({"ERROR"}, "The preview has no DEF bones left to export.")
                return {"CANCELLED"}
            profile.export_bones.clear()
            for bone_name, parent_name in rows:
                row = profile.export_bones.add()
                row.name = bone_name
                row.parent_name = parent_name
            profile.source_signature = current_signature
            profile.configuration_valid = True
            profile.preview_valid = False
            try:
                standalone.embed_file(context.scene)
            except Exception as error:
                profile.preview_valid = True
                self.report({"ERROR"}, f"Could not embed the exporter: {error}")
                return {"CANCELLED"}
            core.remove_preview_object(profile)
        elif not _configuration_is_current(rig, profile):
            self.report({"ERROR"}, "Gen Game Rig before adding this profile to the UI.")
            return {"CANCELLED"}
        else:
            try:
                standalone.embed_file(context.scene)
            except Exception as error:
                self.report({"ERROR"}, f"Could not embed the exporter: {error}")
                return {"CANCELLED"}

        profile.attached = False

        # Restore focus to the source rig after deleting a preview that may have
        # been active in Edit Mode.
        if source_object != rig:
            if rig.mode != "OBJECT":
                context.view_layer.objects.active = rig
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.select_all(action="DESELECT")
            rig.select_set(True)
            context.view_layer.objects.active = rig
        profile_index = next(
            (index for index, candidate in enumerate(rig.rex_profiles) if candidate.uid == profile.uid),
            -1,
        )
        if profile_index >= 0:
            rig.rex_profile_index = profile_index
            rig.rex_profile_dropdown = profile.uid
        context.scene.rex_main_profile_uid = profile.uid
        self.report({"INFO"}, f"Saved {len(profile.export_bones)} DEF bones to the Item tab and embedded the exporter.")
        return {"FINISHED"}


class REX_OT_add_action(bpy.types.Operator):
    bl_idname = "rex.add_action"
    bl_label = "Add Action"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()

    @classmethod
    def poll(cls, context):
        rig = _control_rig_for_context(context)
        return bool(rig and any(profile.action_to_add for profile in rig.rex_profiles))

    def execute(self, context):
        rig, profile = _profile_for_uid(self.profile_uid)
        if not profile or not profile.action_to_add:
            return {"CANCELLED"}
        added = core.add_selected_action_slots(rig, profile, profile.action_to_add)
        profile.action_to_add = None
        if added:
            profile.action_index = max(
                index for index, item in enumerate(profile.action_items) if item.user_added
            )
            self.report({"INFO"}, f"Added {added} Action slot(s).")
            return {"FINISHED"}
        self.report({"WARNING"}, "That Action is already in the Action Bakery list or has no slot for this Rigify rig.")
        return {"CANCELLED"}


class REX_OT_add_actions(bpy.types.Operator):
    bl_idname = "rex.add_actions"
    bl_label = "Add Actions"
    bl_description = "Choose multiple Actions to add to the Action Bakery list"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()

    @classmethod
    def poll(cls, context):
        rig, profile = _profile_for_uid(
            getattr(context.scene, "rex_main_profile_uid", "")
        )
        if not profile:
            rig = _control_rig_for_context(context)
            profile = _selected_profile(rig)
        return bool(rig and profile)

    def invoke(self, context, event):
        rig, profile = _profile_for_uid(self.profile_uid)
        if not rig or not profile:
            rig = _control_rig_for_context(context)
            profile = _profile_by_uid(rig, self.profile_uid)
        if not rig or not profile:
            self.report({"ERROR"}, "Select a valid export profile first.")
            return {"CANCELLED"}
        _populate_action_picker(context, rig, profile)
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        choices = context.window_manager.rex_action_picker_items
        if not choices:
            self.layout.label(text="No compatible Actions are available.", icon="INFO")
            return
        self.layout.prop(
            context.window_manager,
            "rex_action_picker_search",
            text="Search",
            icon="VIEWZOOM",
        )
        self.layout.template_list(
            "REX_UL_ActionChoices",
            "rex_action_picker",
            context.window_manager,
            "rex_action_picker_items",
            context.window_manager,
            "rex_action_picker_index",
            rows=min(10, max(3, len(choices))),
        )

    def execute(self, context):
        rig, profile = _profile_for_uid(self.profile_uid)
        if not rig or not profile:
            rig = _control_rig_for_context(context)
            profile = _profile_by_uid(rig, self.profile_uid)
        if not rig or not profile:
            self.report({"ERROR"}, "Select a valid export profile first.")
            return {"CANCELLED"}

        selected_actions = [
            choice.action
            for choice in context.window_manager.rex_action_picker_items
            if choice.selected and choice.action
        ]
        added = sum(
            core.add_selected_action_slots(rig, profile, action)
            for action in selected_actions
        )
        context.window_manager.rex_action_picker_items.clear()
        if not added:
            self.report({"WARNING"}, "Select at least one new Action to add.")
            return {"CANCELLED"}
        profile.action_index = max(
            index for index, item in enumerate(profile.action_items) if item.user_added
        )
        self.report({"INFO"}, f"Added {added} Action slot(s).")
        return {"FINISHED"}


class REX_OT_remove_action_item(bpy.types.Operator):
    bl_idname = "rex.remove_action_item"
    bl_label = "Remove Action"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()
    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        rig = _control_rig_for_context(context)
        return bool(rig and rig.rex_profiles)

    def execute(self, context):
        rig = _control_rig_for_context(context)
        profile_rig, profile = _profile_for_uid(self.profile_uid)
        rig = profile_rig or rig
        index = self.index if self.index >= 0 else (profile.action_index if profile else -1)
        if not profile or not (0 <= index < len(profile.action_items)):
            return {"CANCELLED"}
        profile.action_items.remove(index)
        profile.action_index = min(index, max(0, len(profile.action_items) - 1))
        return {"FINISHED"}


class REX_OT_move_action_item(bpy.types.Operator):
    bl_idname = "rex.move_action_item"
    bl_label = "Move Action"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()
    index: IntProperty(default=-1)
    direction: IntProperty(default=1, min=-1, max=1)

    @classmethod
    def poll(cls, context):
        rig = _control_rig_for_context(context)
        return bool(rig and rig.rex_profiles)

    def execute(self, context):
        rig = _control_rig_for_context(context)
        profile_rig, profile = _profile_for_uid(self.profile_uid)
        rig = profile_rig or rig
        index = self.index if self.index >= 0 else (profile.action_index if profile else -1)
        if not profile or not (0 <= index < len(profile.action_items)):
            return {"CANCELLED"}
        visible = [index for index, item in enumerate(profile.action_items) if item.user_added]
        if index not in visible:
            return {"CANCELLED"}
        visible_position = visible.index(index)
        target_position = visible_position + self.direction
        if target_position < 0 or target_position >= len(visible):
            return {"CANCELLED"}
        source_index = index
        target_index = visible[target_position]
        profile.action_items.move(source_index, target_index)
        profile.action_index = target_index
        return {"FINISHED"}


class REX_OT_add_selected_meshes(bpy.types.Operator):
    bl_idname = "rex.add_selected_meshes"
    bl_label = "Add Selected Meshes"
    bl_description = "Add selected mesh objects to this profile's FBX export list"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()

    @classmethod
    def poll(cls, context):
        rig = _control_rig_for_context(context)
        return bool(rig and any(obj.type == "MESH" for obj in context.selected_objects))

    def execute(self, context):
        rig = _control_rig_for_context(context)
        profile_rig, profile = _profile_for_uid(self.profile_uid)
        rig = profile_rig or rig
        if not profile:
            return {"CANCELLED"}
        profile.mesh_list_initialized = True
        added = 0
        last_index = -1
        for mesh in context.selected_objects:
            if mesh.type != "MESH":
                continue
            item_index = next(
                (i for i, item in enumerate(profile.mesh_items) if item.mesh == mesh),
                -1,
            )
            if item_index >= 0:
                profile.mesh_items[item_index].enabled = True
                last_index = item_index
                continue
            profile.mesh_items.add().mesh = mesh
            last_index = len(profile.mesh_items) - 1
            added += 1
        profile.mesh_index = max(0, last_index)
        self.report({"INFO"}, f"Added {added} mesh(es) to the export list.")
        return {"FINISHED"}


class REX_OT_add_bound_meshes(bpy.types.Operator):
    bl_idname = "rex.add_bound_meshes"
    bl_label = "Add Rig Meshes"
    bl_description = "Add every mesh with an Armature modifier targeting this Rigify rig"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()

    @classmethod
    def poll(cls, context):
        return _control_rig_for_context(context) is not None

    def execute(self, context):
        rig = _control_rig_for_context(context)
        profile_rig, profile = _profile_for_uid(self.profile_uid)
        rig = profile_rig or rig
        if not profile:
            return {"CANCELLED"}
        profile.mesh_list_initialized = True
        added = 0
        last_index = -1
        for mesh in core.associated_meshes(context.scene, rig):
            item_index = next(
                (i for i, item in enumerate(profile.mesh_items) if item.mesh == mesh),
                -1,
            )
            if item_index >= 0:
                profile.mesh_items[item_index].enabled = True
                last_index = item_index
                continue
            profile.mesh_items.add().mesh = mesh
            last_index = len(profile.mesh_items) - 1
            added += 1
        profile.mesh_index = max(0, last_index)
        self.report({"INFO"}, f"Added {added} rig mesh(es) to the export list.")
        return {"FINISHED"}


class REX_OT_remove_mesh_item(bpy.types.Operator):
    bl_idname = "rex.remove_mesh_item"
    bl_label = "Remove Mesh"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()

    @classmethod
    def poll(cls, context):
        rig = _control_rig_for_context(context)
        return bool(rig and rig.rex_profiles)

    def execute(self, context):
        rig = _control_rig_for_context(context)
        profile_rig, profile = _profile_for_uid(self.profile_uid)
        rig = profile_rig or rig
        if not profile or not (0 <= profile.mesh_index < len(profile.mesh_items)):
            return {"CANCELLED"}
        profile.mesh_items.remove(profile.mesh_index)
        profile.mesh_index = min(profile.mesh_index, max(0, len(profile.mesh_items) - 1))
        profile.mesh_list_initialized = True
        return {"FINISHED"}


class REX_OT_set_action_range(bpy.types.Operator):
    bl_idname = "rex.set_action_range"
    bl_label = "Use Action Frame Range"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()
    index: IntProperty(min=0)

    @classmethod
    def poll(cls, context):
        return _control_rig_for_context(context) is not None

    def execute(self, context):
        rig, profile = _profile_for_uid(self.profile_uid)
        if not profile or self.index >= len(profile.action_items):
            return {"CANCELLED"}
        item = profile.action_items[self.index]
        if not item.action:
            return {"CANCELLED"}
        item.set_frame_start = int(item.action.frame_range[0])
        item.set_frame_end = int(item.action.frame_range[1])
        item.set_frame_range_initialized = True
        return {"FINISHED"}


class REX_OT_export_selected_actions(bpy.types.Operator):
    bl_idname = "rex.export_selected_actions"
    bl_label = "Export FBX"
    bl_options = {"UNDO"}

    profile_uid: StringProperty()

    @classmethod
    def poll(cls, context):
        return _control_rig_for_context(context) is not None

    def execute(self, context):
        rig, profile = _profile_for_uid(self.profile_uid)
        if not profile:
            self.report({"ERROR"}, "Export profile not found.")
            return {"CANCELLED"}
        if not _configuration_is_current(rig, profile):
            self.report({"ERROR"}, "Gen Game Rig and Save to UI in Rigify Export first.")
            return {"CANCELLED"}

        items = [
            item
            for item in profile.action_items
            if item.user_added and item.enabled and item.action
        ]
        if profile.include_mesh and not core.selected_profile_meshes(profile):
            self.report({"ERROR"}, "Include Mesh is enabled, but no mesh is enabled in the profile's mesh list.")
            return {"CANCELLED"}
        if not core.better_fbx_available():
            self.report(
                {"ERROR"},
                "Better FBX is not enabled. Enable the Better FBX add-on and try again.",
            )
            return {"CANCELLED"}

        if not items:
            try:
                filepath = core.export_static_fbx(context, rig, profile)
            except Exception as error:
                self.report({"ERROR"}, f"Static FBX export failed: {error}")
                print("Rigify Export error:\n" + traceback.format_exc())
                return {"CANCELLED"}
            self.report({"INFO"}, f"Exported static FBX without animation: {filepath}")
            return {"FINISHED"}

        if getattr(profile, "pack_actions_into_nla", False):
            try:
                filepath = core.export_actions_packed_nla(
                    context, rig, profile, items
                )
            except Exception as error:
                self.report({"ERROR"}, f"Packed Action export failed: {error}")
                print("Rigify Export error:\n" + traceback.format_exc())
                return {"CANCELLED"}
            self.report(
                {"INFO"},
                f"Packed {len(items)} Action(s) into one FBX: {filepath}",
            )
            return {"FINISHED"}

        exported = []
        errors = []
        for item in items:
            try:
                exported.append(
                    core.export_one_action(
                        context,
                        rig,
                        profile,
                        item.action,
                        item.action_slot_identifier,
                        item,
                    )
                )
            except Exception as error:
                errors.append(f"{item.action.name}: {error}")
                print("Rigify Export error:\n" + traceback.format_exc())
        if errors:
            self.report({"ERROR"}, "Some Action exports failed: " + "; ".join(errors[:3]))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {len(exported)} Unity FBX file(s).")
        return {"FINISHED"}


class REX_UL_ActionItems(bpy.types.UIList):
    bl_idname = "REX_UL_ActionItems"

    def filter_items(self, _context, data, property_name):
        items = getattr(data, property_name)
        return [self.bitflag_filter_item if item.user_added else 0 for item in items], []

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="", emboss=True)
        action_name = item.action.name if item.action else "Missing Action"
        if item.action and item.action_slot_identifier and len(item.action.slots) > 1:
            action_name += " / " + item.action_slot_identifier
        row.label(text=action_name, icon="ACTION")


class REX_UL_ActionChoices(bpy.types.UIList):
    bl_idname = "REX_UL_ActionChoices"

    def filter_items(self, context, data, property_name):
        items = getattr(data, property_name)
        query = context.window_manager.rex_action_picker_search.strip().casefold()
        flags = [
            self.bitflag_filter_item
            if _action_choice_matches(item.action, query)
            else 0
            for item in items
        ]
        return flags, []

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "selected", text="", emboss=True)
        row.label(text=item.action.name if item.action else "Missing Action", icon="ACTION")


class REX_UL_MeshItems(bpy.types.UIList):
    bl_idname = "REX_UL_MeshItems"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="", emboss=True)
        row.label(
            text=item.mesh.name if item.mesh else "Missing Mesh",
            icon="OUTLINER_OB_MESH",
        )


class REX_PT_profiles(bpy.types.Panel):
    bl_label = "Rigify Export"
    bl_idname = "REX_PT_profiles"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Rigify Export"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        layout = self.layout
        profile_row = layout.row(align=True)
        profile_row.prop(context.scene, "rex_main_profile_uid", text="Export Profile")
        profile_row.operator("rex.refresh_profiles", text="", icon="FILE_REFRESH")
        profile_row.operator("rex.new_profile", text="", icon="ADD")
        embedded_count = len(standalone.embedded_profile_entries())
        if embedded_count:
            layout.label(
                text=f"Embedded script profiles: {embedded_count}",
                icon="FILE_SCRIPT",
            )
        else:
            layout.label(text="No embedded script profiles found.", icon="INFO")

        selection = context.scene.rex_main_profile_uid
        if selection == "NONE":
            layout.label(text="No profiles yet. Use New to create one.", icon="INFO")
            return

        rig, profile = _profile_for_uid(selection)
        if not profile:
            layout.label(text="Profile is not loaded. Press Refresh to reload embedded profiles.", icon="ERROR")
            return

        if not profile.preview_object and profile.source_rig:
            core.remove_preview_object(profile)

        layout.separator()
        layout.prop(profile, "name")
        control_rig_row = layout.row(align=True)
        control_rig_row.enabled = _control_rig_editable(profile)
        control_rig_row.prop(profile, "control_rig", text="Control Rig")
        game_rig_row = layout.row(align=True)
        game_rig_row.enabled = False
        game_rig_row.prop(profile, "game_rig", text="Game Rig")
        layout.label(
            text=(
                f"{len(profile.export_bones)} export bone(s), "
                f"{len(profile.mesh_items)} mesh(es), "
                f"{sum(1 for item in profile.action_items if item.user_added)} Action(s)"
            ),
            icon="INFO",
        )
        layout.prop(profile, "hierarchy_mode", text="Hierarchy Settings")
        layout.prop(profile, "disconnect_bones")

        preview = profile.preview_object
        if preview:
            preview_box = layout.box()
            preview_box.label(
                text=f"Game Rig: {len(preview.data.bones)} DEF bones",
                icon="ARMATURE_DATA",
            )
            preview_box.label(text="Inspect the bones and delete any DEF bones you do not need.")
            focus = preview_box.operator("rex.focus_preview", text="Select Game Rig", icon="RESTRICT_SELECT_OFF")
            focus.profile_uid = profile.uid
            if not rig or not profile.preview_valid or profile.preview_signature != core.hierarchy_signature(
                rig, profile.hierarchy_mode, profile.disconnect_bones
            ):
                preview_box.label(text="Hierarchy settings changed; regenerate the Game Rig.", icon="ERROR")
        elif rig and profile.configuration_valid:
            status = layout.box()
            status.label(
                text=f"Saved to UI: {len(profile.export_bones)} DEF bones; Game Rig cleared.",
                icon="CHECKMARK" if _configuration_is_current(rig, profile) else "ERROR",
            )
        else:
            layout.label(text="Gen Game Rig to review the DEF bones.", icon="INFO")

        if not rig:
            layout.label(text="Choose a Rigify armature in Control Rig before generating.", icon="INFO")
        mesh_section = layout.column()
        mesh_section.enabled = rig is not None
        _draw_mesh_list(mesh_section, profile)

        generate_row = layout.row()
        generate_row.enabled = bool(rig or core.is_rigify_rig(profile.control_rig))
        generate = generate_row.operator(
            "rex.generate_preview",
            text="ReGen Game Rig" if profile.preview_object else "Gen Game Rig",
            icon="ARMATURE_DATA",
        )
        generate.profile_uid = profile.uid
        if preview:
            save_to_ui = layout.operator("rex.save_profile", text="Save to UI", icon="FILE_TICK")
            save_to_ui.profile_uid = profile.uid
        elif rig and _configuration_is_current(rig, profile):
            status = layout.row()
            status.enabled = False
            status.label(text="Profile is available in the Item tab.", icon="CHECKMARK")

        delete_row = layout.row()
        delete = delete_row.operator("rex.delete_profile", text="Delete Profile", icon="TRASH")
        delete.profile_uid = profile.uid


class REX_PT_export(bpy.types.Panel):
    bl_label = "Rigify Export"
    bl_idname = "REX_PT_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Item"
    bl_order = 100

    @classmethod
    def poll(cls, context):
        return _control_rig_for_context(context) is not None

    def draw(self, context):
        rig = _control_rig_for_context(context)
        layout = self.layout
        if not rig or not rig.rex_profiles:
            layout.label(text="Create an export profile in the Rigify Export tab.", icon="INFO")
            return

        row = layout.row(align=True)
        row.prop(rig, "rex_profile_dropdown", text="Export Profile")
        profile = _selected_profile(rig)
        if not profile:
            layout.label(text="Select an export profile.", icon="INFO")
            return

        layout.label(text=profile.name, icon="ARMATURE_DATA")
        if not _configuration_is_current(rig, profile):
            layout.label(
                text="Generate and save this profile's DEF bone set first.",
                icon="ERROR",
            )
        _draw_action_bakery(layout, profile)
        has_export_actions = any(
            item.user_added and item.enabled and item.action
            for item in profile.action_items
        )
        if not has_export_actions:
            layout.label(
                text="No enabled Actions; export will have no animation.",
                icon="INFO",
            )

        export_options = layout.row(align=True)
        export_options.prop(profile, "include_mesh", text="Include Mesh")
        if profile.include_mesh:
            mesh_count = len(core.selected_profile_meshes(profile))
            mesh_word = "mesh" if mesh_count == 1 else "meshes"
            export_options.label(text=f"{mesh_count} {mesh_word}")
        pack_option = export_options.row(align=True)
        pack_option.enabled = has_export_actions
        pack_option.prop(profile, "pack_actions_into_nla", text="Pack into NLA")
        layout.prop(profile, "output_directory", text="Output Folder")
        layout.prop(profile, "better_fbx_preset", text="Preset")
        if profile.pack_actions_into_nla and has_export_actions:
            if not profile.pack_file_name:
                profile.pack_file_name = core.default_pack_file_name(profile.name)
            layout.prop(profile, "pack_file_name", text="File Name")
            layout.label(text="Enabled Actions will export as NLA tracks in one FBX.")
        if not core.better_fbx_available():
            layout.label(
                text="Enable Better FBX to export files.",
                icon="ERROR",
            )

        export = layout.row()
        export.enabled = _configuration_is_current(rig, profile) and (
            not profile.include_mesh or bool(core.selected_profile_meshes(profile))
        ) and core.better_fbx_available()
        operator = export.operator(
            "rex.export_selected_actions", text="Export FBX", icon="EXPORT"
        )
        operator.profile_uid = profile.uid


# The original profile panel's draw body only depends on ``self.layout``. This
# adapter lets Script Toolkit reuse it without changing its contents.
class _PanelLayoutAdapter:
    def __init__(self, layout):
        self.layout = layout


def draw_ui(layout, context):
    REX_PT_profiles.draw(_PanelLayoutAdapter(layout), context)


_BASE_CLASSES = (
    REX_OT_refresh_profiles,
    REX_OT_new_profile,
    REX_OT_delete_profile,
    REX_OT_generate_preview,
    REX_OT_focus_preview,
    REX_OT_save_profile,
    REX_OT_add_action,
    REX_OT_add_actions,
    REX_OT_remove_action_item,
    REX_OT_move_action_item,
    REX_OT_add_selected_meshes,
    REX_OT_add_bound_meshes,
    REX_OT_remove_mesh_item,
    REX_OT_set_action_range,
    REX_OT_export_selected_actions,
    REX_UL_ActionItems,
    REX_UL_ActionChoices,
    REX_UL_MeshItems,
)


REGISTER_CLASSES = (*_BASE_CLASSES, REX_PT_export)


CLASSES = (
    *_BASE_CLASSES,
    REX_PT_profiles,
    REX_PT_export,
)


def register(register_panels=False):
    _panel_classes.clear()
    needs_refresh = False
    classes_to_register = CLASSES if register_panels else REGISTER_CLASSES
    for cls in reversed(CLASSES):
        registered_cls = _registered_class(cls)
        if registered_cls is not None and (
            cls not in classes_to_register or registered_cls is not cls
        ):
            try:
                bpy.utils.unregister_class(registered_cls)
                needs_refresh = True
            except RuntimeError:
                pass
    for cls in classes_to_register:
        registered_cls = _registered_class(cls)
        if registered_cls is None:
            bpy.utils.register_class(cls)
            registered_cls = cls
            needs_refresh = True
        _panel_classes.append(registered_cls)

    for handler in tuple(bpy.app.handlers.load_post):
        if (
            handler is not _load_post
            and getattr(handler, "__module__", None) == __name__
            and getattr(handler, "__name__", None) == "_load_post"
        ):
            bpy.app.handlers.load_post.remove(handler)
            needs_refresh = True
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)
        needs_refresh = True
    for handler in tuple(bpy.app.handlers.depsgraph_update_post):
        if (
            handler is not _schedule_preview_collection_cleanup
            and getattr(handler, "__module__", None) == __name__
            and getattr(handler, "__name__", None)
            == "_schedule_preview_collection_cleanup"
        ):
            bpy.app.handlers.depsgraph_update_post.remove(handler)
            needs_refresh = True
    if _schedule_preview_collection_cleanup not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(
            _schedule_preview_collection_cleanup
        )
        needs_refresh = True
    if needs_refresh:
        if not bpy.app.timers.is_registered(_initialize_after_register):
            bpy.app.timers.register(_initialize_after_register, first_interval=0.0)


def unregister():
    if bpy.app.timers.is_registered(_initialize_after_register):
        bpy.app.timers.unregister(_initialize_after_register)
    if bpy.app.timers.is_registered(_cleanup_empty_preview_collection):
        bpy.app.timers.unregister(_cleanup_empty_preview_collection)
    for handler in tuple(bpy.app.handlers.load_post):
        if (
            handler is _load_post
            or (
                getattr(handler, "__module__", None) == __name__
                and getattr(handler, "__name__", None) == "_load_post"
            )
        ):
            bpy.app.handlers.load_post.remove(handler)
    for handler in tuple(bpy.app.handlers.depsgraph_update_post):
        if (
            handler is _schedule_preview_collection_cleanup
            or (
                getattr(handler, "__module__", None) == __name__
                and getattr(handler, "__name__", None)
                == "_schedule_preview_collection_cleanup"
            )
        ):
            bpy.app.handlers.depsgraph_update_post.remove(handler)
    for cls in reversed(CLASSES):
        registered_cls = _registered_class(cls)
        if registered_cls is None:
            continue
        try:
            bpy.utils.unregister_class(registered_cls)
        except RuntimeError:
            pass
    _panel_classes.clear()
