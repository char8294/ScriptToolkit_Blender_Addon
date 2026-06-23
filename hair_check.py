"""Check Hair And Cap tool, embedded in Script Toolkit without a separate panel tab."""

import re
import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup, UIList


_STATE_DEFAULTS = {
    "running": False,
    "scene_name": "",
    "view_layer_name": "",
    "objects": [],
    "hat_name": "",
    "original_visibility": {},
    "collection_visibility": {},
    "layer_collection_visibility": [],
    "index": -1,
    "run_id": 0,
    "timer_callback": None,
}
_STATE = bpy.app.driver_namespace.setdefault("script_toolkit_hair_runtime", {})
for _key, _value in _STATE_DEFAULTS.items():
    _STATE.setdefault(_key, _value)


def _scene():
    return bpy.data.scenes.get(_STATE["scene_name"])


def _cancel_timer():
    callback = _STATE.get("timer_callback")
    if callback is not None:
        try:
            if bpy.app.timers.is_registered(callback):
                bpy.app.timers.unregister(callback)
        except Exception:
            pass
    _STATE["timer_callback"] = None


def _register_timer(delay_seconds):
    _cancel_timer()
    run_id = _STATE["run_id"]

    def callback():
        return _cycle_step(run_id)

    _STATE["timer_callback"] = callback
    bpy.app.timers.register(callback, first_interval=max(delay_seconds, 0.01))


def _active(scene=None):
    return _STATE["running"] and (scene or _scene()) is not None and (scene or _scene()).name == _STATE["scene_name"]


def _view_layer(scene=None):
    scene = scene or _scene()
    if scene:
        stored_name = _STATE.get("view_layer_name", "")
        return scene.view_layers.get(stored_name) or bpy.context.view_layer
    return bpy.context.view_layer


def _set_visible(obj, visible, view_layer=None):
    if obj:
        try:
            obj.hide_set(not visible, view_layer=view_layer)
        except TypeError:
            obj.hide_set(not visible)
        obj.hide_viewport = not visible
        obj.hide_render = not visible


def _targets(settings):
    if not settings.hair_collection:
        return []
    objects = []
    seen = set()
    def sequence_key(name):
        """Sort numbered Outliner names naturally: H001, H002, ... H010."""
        return tuple(
            (0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in re.split(r"(\d+)", name)
        )

    def walk(collection):
        for obj in collection.objects:
            if obj != settings.hat_object and obj.type == "MESH" and obj not in seen:
                objects.append(obj)
                seen.add(obj)
        for child in collection.children:
            walk(child)
    walk(settings.hair_collection)
    # Sort every mesh together, even when some live in nested child collections.
    return sorted(objects, key=lambda item: sequence_key(item.name))


def _refresh_sequence(settings):
    targets = _targets(settings)
    names = [obj.name for obj in targets]
    if names == [item.object_name for item in settings.sequence_items]:
        return targets
    settings.sequence_items.clear()
    for name in names:
        item = settings.sequence_items.add()
        item.object_name = name
    settings.sequence_index = 0
    return targets


def _on_hair_collection_changed(settings, _context):
    _refresh_sequence(settings)


def _restore_visibility():
    view_layer = _view_layer()
    for name, state in _STATE["original_visibility"].items():
        obj = bpy.data.objects.get(name)
        if obj:
            try:
                obj.hide_set(state["hide_set"], view_layer=view_layer)
            except TypeError:
                obj.hide_set(state["hide_set"])
            obj.hide_viewport = state["hide_viewport"]
            obj.hide_render = state["hide_render"]


def _collection_path(root, target, ancestors=()):
    """Return a parent-to-target path for a collection inside the scene tree."""
    path = ancestors + (root,)
    if root == target:
        return path
    for child in root.children:
        found = _collection_path(child, target, path)
        if found:
            return found
    return ()


def _related_collections(scene, target):
    """Include the target collection, all descendants, and parent collections that can hide it."""
    collections = set(_collection_path(scene.collection, target))

    def add_children(collection):
        collections.add(collection)
        for child in collection.children:
            add_children(child)

    add_children(target)
    return collections


def _layer_collections_for(scene, collections):
    found = []

    def walk(layer_collection):
        if layer_collection.collection in collections:
            found.append(layer_collection)
        for child in layer_collection.children:
            walk(child)

    for view_layer in scene.view_layers:
        walk(view_layer.layer_collection)
    return found


def _open_hair_collection(scene, collection):
    """Temporarily open the Outliner Eye for the selected collection and its path."""
    collections = _related_collections(scene, collection)
    _STATE["collection_visibility"] = {item: item.hide_viewport for item in collections}
    _STATE["layer_collection_visibility"] = []
    for item in collections:
        item.hide_viewport = False
    for layer_collection in _layer_collections_for(scene, collections):
        try:
            _STATE["layer_collection_visibility"].append((
                layer_collection,
                layer_collection.hide_viewport,
                layer_collection.exclude,
            ))
            # Excluded collections do not show their meshes even when every Object Eye is enabled.
            layer_collection.exclude = False
            layer_collection.hide_viewport = False
        except Exception:
            pass


def _restore_collection_visibility():
    for collection, hidden in _STATE["collection_visibility"].items():
        try:
            collection.hide_viewport = hidden
        except Exception:
            pass
    for layer_collection, hidden, excluded in reversed(_STATE["layer_collection_visibility"]):
        try:
            layer_collection.hide_viewport = hidden
            layer_collection.exclude = excluded
        except Exception:
            pass


def _update_view_layer(view_layer):
    try:
        view_layer.update()
    except Exception:
        pass


def _reset(scene=None, current_name=""):
    scene = scene or _scene()
    if scene and hasattr(scene, "script_toolkit_hair_settings"):
        scene.script_toolkit_hair_settings.is_running = False
        scene.script_toolkit_hair_settings.current_object_name = current_name


def stop(mark_finished=False, fallback_scene=None):
    scene = _scene() or fallback_scene
    current_name = ""
    if mark_finished and _STATE["objects"]:
        current_name = _STATE["objects"][min(max(_STATE["index"], 0), len(_STATE["objects"]) - 1)]
    if scene and hasattr(scene, "script_toolkit_hair_settings") and scene.script_toolkit_hair_settings.restore_when_done:
        _restore_visibility()
    # Collection Eye state is always restored: it is only opened temporarily for this tool.
    _restore_collection_visibility()
    _reset(scene, current_name)
    _cancel_timer()
    _STATE.update({
        "running": False,
        "scene_name": "",
        "view_layer_name": "",
        "objects": [],
        "hat_name": "",
        "original_visibility": {},
        "collection_visibility": {},
        "layer_collection_visibility": [],
        "index": -1,
    })
    _STATE["run_id"] += 1


def _cycle_step(expected_run_id=None):
    if expected_run_id is not None and expected_run_id != _STATE["run_id"]:
        return None
    if not _STATE["running"]:
        return None
    scene = _scene()
    if not scene:
        stop()
        return None
    try:
        settings = scene.script_toolkit_hair_settings
        objects = [bpy.data.objects.get(name) for name in _STATE["objects"]]
        objects = [obj for obj in objects if obj]
        if not objects:
            stop(fallback_scene=scene)
            return None
        _STATE["objects"] = [obj.name for obj in objects]
        view_layer = _view_layer(scene)
        _set_visible(bpy.data.objects.get(_STATE["hat_name"]), True, view_layer)
        _STATE["index"] += 1
        if _STATE["index"] >= len(objects):
            stop(mark_finished=True, fallback_scene=scene)
            return None
        current = objects[_STATE["index"]]
        # Isolate one mesh for viewport/render checking, following the sorted sequence.
        for obj in objects:
            _set_visible(obj, False, view_layer)
        _set_visible(current, True, view_layer)
        _update_view_layer(view_layer)
        settings.current_object_name = current.name
        settings.sequence_index = min(_STATE["index"], max(0, len(settings.sequence_items) - 1))
        return max(settings.delay_seconds, 0.01)
    except Exception as exc:
        print(f"[Script Toolkit / Hair Check] Timer error: {exc}")
        stop(fallback_scene=scene)
        return None


class STHC_SequenceItem(PropertyGroup):
    object_name: StringProperty()


class STHC_Settings(PropertyGroup):
    hat_object: PointerProperty(name="Hat Object", type=bpy.types.Object, description="Object that remains visible while cycling hair objects")
    hair_collection: PointerProperty(
        name="Hair Collection",
        type=bpy.types.Collection,
        description="Collection containing hair objects to cycle",
        update=_on_hair_collection_changed,
    )
    delay_seconds: FloatProperty(name="Delay", description="Seconds before switching to the next hair object", default=0.75, min=0.01, soft_max=10.0)
    restore_when_done: BoolProperty(name="Restore When Done", description="Restore original visibility when the cycle stops or finishes", default=True)
    is_running: BoolProperty(name="Running", default=False, options={"HIDDEN"})
    current_object_name: StringProperty(name="Current Object", default="", options={"HIDDEN"})
    sequence_items: CollectionProperty(type=STHC_SequenceItem)
    sequence_index: IntProperty(default=0)


class STHC_UL_sequence(UIList):
    bl_idname = "STHC_UL_sequence"

    def draw_item(self, _context, layout, _data, item, _icon, _active_data, _active_propname, _index):
        layout.label(text=item.object_name, icon="MESH_DATA")


class STHC_OT_pin_active_hat(Operator):
    bl_idname = "script_toolkit.hair_pin_active_hat"
    bl_label = "Pin Active Object"
    bl_description = "Use the active object as the pinned hat object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        context.scene.script_toolkit_hair_settings.hat_object = context.active_object
        self.report({"INFO"}, f"Pinned: {context.active_object.name}")
        return {"FINISHED"}


class STHC_OT_clear_hat(Operator):
    bl_idname = "script_toolkit.hair_clear_hat"
    bl_label = "Clear Pin"
    bl_description = "Clear the pinned hat object"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.script_toolkit_hair_settings.hat_object = None
        return {"FINISHED"}


class STHC_OT_start_cycle(Operator):
    bl_idname = "script_toolkit.hair_start_cycle"
    bl_label = "Start Check"
    bl_description = "Start cycling through hair objects"

    def execute(self, context):
        scene = context.scene
        settings = scene.script_toolkit_hair_settings
        if settings.is_running and not _active(scene):
            _reset(scene)
        if _STATE["running"]:
            self.report({"WARNING"}, "Hair check is already running")
            return {"CANCELLED"}
        if not settings.hair_collection:
            self.report({"ERROR"}, "Please choose a Hair Collection")
            return {"CANCELLED"}
        objects = _targets(settings)
        if not objects:
            self.report({"ERROR"}, "No valid objects found in the Hair Collection")
            return {"CANCELLED"}
        _refresh_sequence(settings)
        tracked = list(objects)
        if settings.hat_object:
            tracked.append(settings.hat_object)
        _STATE.update({
            "running": True, "scene_name": scene.name, "view_layer_name": context.view_layer.name,
            "objects": [obj.name for obj in objects],
            "hat_name": settings.hat_object.name if settings.hat_object else "",
            "original_visibility": {
                obj.name: {
                    "hide_set": obj.hide_get(view_layer=context.view_layer),
                    "hide_viewport": obj.hide_viewport,
                    "hide_render": obj.hide_render,
                }
                for obj in tracked
            },
            "index": -1,
        })
        _STATE["run_id"] += 1
        settings.is_running = True
        settings.current_object_name = ""
        _open_hair_collection(scene, settings.hair_collection)
        # Reset every candidate before the first check.  _cycle_step opens H001/H002/…
        # in sequence for both the viewport and render visibility.
        for obj in objects:
            _set_visible(obj, False, context.view_layer)
        _set_visible(settings.hat_object, True, context.view_layer)
        _update_view_layer(context.view_layer)

        # Show the first mesh synchronously; subsequent meshes advance after the delay.
        _cycle_step()
        _register_timer(settings.delay_seconds)
        sequence = " → ".join(obj.name for obj in objects)
        print(f"[Script Toolkit / Hair Check] Sequence ({len(objects)}): {sequence}")
        self.report({"INFO"}, f"Started {len(objects)} meshes — first: {objects[0].name}")
        return {"FINISHED"}


class STHC_OT_stop_cycle(Operator):
    bl_idname = "script_toolkit.hair_stop_cycle"
    bl_label = "Stop Check"
    bl_description = "Stop cycling through hair objects"

    def execute(self, context):
        settings = context.scene.script_toolkit_hair_settings
        if not _STATE["running"]:
            if settings.is_running:
                _reset(context.scene)
                self.report({"INFO"}, "Recovered UI state")
                return {"FINISHED"}
            self.report({"WARNING"}, "Hair check is not running")
            return {"CANCELLED"}
        stop(fallback_scene=context.scene)
        self.report({"INFO"}, "Hair check stopped")
        return {"FINISHED"}


class STHC_OT_refresh_sequence(Operator):
    bl_idname = "script_toolkit.hair_refresh_sequence"
    bl_label = "Refresh Sequence"
    bl_description = "Refresh the sorted mesh list from the selected Hair Collection"

    def execute(self, context):
        targets = _refresh_sequence(context.scene.script_toolkit_hair_settings)
        self.report({"INFO"}, f"Sequence refreshed: {len(targets)} mesh(es)")
        return {"FINISHED"}


def draw_ui(layout, context):
    settings = context.scene.script_toolkit_hair_settings
    running = _active(context.scene)
    col = layout.column(align=True)
    col.prop(settings, "hat_object")
    row = col.row(align=True)
    row.operator("script_toolkit.hair_pin_active_hat", icon="PINNED")
    row.operator("script_toolkit.hair_clear_hat", icon="X")
    layout.separator()
    col = layout.column(align=True)
    col.prop(settings, "hair_collection")
    col.prop(settings, "delay_seconds")
    col.prop(settings, "restore_when_done")
    if settings.hair_collection:
        targets = _refresh_sequence(settings)
        preview = layout.box()
        row = preview.row(align=True)
        row.label(text=f"Sequence Preview ({len(targets)} mesh)", icon="SORTALPHA")
        row.operator("script_toolkit.hair_refresh_sequence", text="", icon="FILE_REFRESH")
        if targets:
            preview.template_list(
                "STHC_UL_sequence", "", settings, "sequence_items", settings, "sequence_index",
                rows=7, maxrows=12,
            )
        else:
            preview.label(text="No mesh found in this collection tree", icon="ERROR")
    layout.separator()
    row = layout.row(align=True)
    row.enabled = not running
    row.operator("script_toolkit.hair_start_cycle", icon="PLAY")
    row = layout.row(align=True)
    row.enabled = running
    row.operator("script_toolkit.hair_stop_cycle", icon="PAUSE")
    box = layout.box()
    box.label(text=f"Status: {'Running' if running else 'Idle'}")
    box.label(text=f"Current: {settings.current_object_name if running and settings.current_object_name else '-'}")
    if running:
        box.label(text=f"Progress: {min(_STATE['index'] + 1, len(settings.sequence_items))}/{len(settings.sequence_items)}")


@persistent
def _load_post(_dummy):
    stop()


CLASSES = (
    STHC_SequenceItem,
    STHC_Settings,
    STHC_UL_sequence,
    STHC_OT_pin_active_hat,
    STHC_OT_clear_hat,
    STHC_OT_start_cycle,
    STHC_OT_stop_cycle,
    STHC_OT_refresh_sequence,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.script_toolkit_hair_settings = PointerProperty(type=STHC_Settings)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)


def unregister():
    stop()
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)
    del bpy.types.Scene.script_toolkit_hair_settings
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
