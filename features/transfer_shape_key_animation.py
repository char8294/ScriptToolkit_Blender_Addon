"""Transfer Shape Key animation curves between mesh Actions."""

from __future__ import annotations

import ast
import re

import bpy
from bpy.props import PointerProperty
from bpy.types import Operator, PropertyGroup


_KEY_BLOCK_PATH_RE = re.compile(r'key_blocks\[("(?:\\.|[^"\\])*")\]')


def _shape_keys(obj):
    if not obj or getattr(obj, "type", None) != "MESH" or not getattr(obj, "data", None):
        return None
    return getattr(obj.data, "shape_keys", None)


def _poll_mesh_with_shape_keys(self, obj):
    return _shape_keys(obj) is not None


def _active_action_for_mesh(obj):
    shape_keys = _shape_keys(obj)
    animation_data = getattr(shape_keys, "animation_data", None)
    if animation_data and animation_data.action:
        return animation_data.action

    animation_data = getattr(obj, "animation_data", None)
    return animation_data.action if animation_data else None


def _update_source_mesh(self, context):
    action = _active_action_for_mesh(self.source_mesh)
    if action:
        self.source_action = action


def _update_target_mesh(self, context):
    action = _active_action_for_mesh(self.target_mesh)
    if action:
        self.target_action = action


class ST_TransferShapeKeyAnimationProperties(PropertyGroup):
    source_mesh: PointerProperty(
        name="Source Mesh",
        description="Mesh that owns the source Shape Key animation",
        type=bpy.types.Object,
        poll=_poll_mesh_with_shape_keys,
        update=_update_source_mesh,
    )
    source_action: PointerProperty(
        name="Source Action",
        description="Action containing the source Shape Key animation",
        type=bpy.types.Action,
    )
    target_mesh: PointerProperty(
        name="Target Mesh",
        description="Mesh that receives the Shape Key animation",
        type=bpy.types.Object,
        poll=_poll_mesh_with_shape_keys,
        update=_update_target_mesh,
    )
    target_action: PointerProperty(
        name="Target Action",
        description="Action that receives the Shape Key animation curves",
        type=bpy.types.Action,
    )


def _key_block_name(data_path):
    match = _KEY_BLOCK_PATH_RE.search(data_path)
    if not match:
        return None
    try:
        return ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return None


def _is_layered_action(action):
    # Blender can expose a newly-created, empty Action as both legacy and
    # layered. Treat it as layered so it can receive its first slot and layer.
    return bool(
        getattr(action, "is_action_layered", False)
        or getattr(action, "is_empty", False)
    )


def _assigned_slot(shape_keys, action):
    animation_data = getattr(shape_keys, "animation_data", None)
    if animation_data and animation_data.action == action:
        return getattr(animation_data, "action_slot", None)
    return None


def _shape_key_slots(action):
    return [
        slot
        for slot in getattr(action, "slots", ())
        if getattr(slot, "target_id_type", None) == "KEY"
    ]


def _source_fcurves(action, shape_keys):
    """Return Shape Key curves, preferring the source Key's active Action slot."""
    if not _is_layered_action(action):
        return [
            curve
            for curve in getattr(action, "fcurves", ())
            if "key_blocks[" in curve.data_path
        ]

    slots = _shape_key_slots(action)
    assigned_slot = _assigned_slot(shape_keys, action)
    if assigned_slot and getattr(assigned_slot, "target_id_type", None) == "KEY":
        slot_handles = {assigned_slot.handle}
    else:
        slot_handles = {slot.handle for slot in slots}

    curves = []
    for layer in getattr(action, "layers", ()):
        for strip in getattr(layer, "strips", ()):
            for channelbag in getattr(strip, "channelbags", ()):
                if slot_handles and channelbag.slot_handle not in slot_handles:
                    continue
                curves.extend(
                    curve
                    for curve in channelbag.fcurves
                    if "key_blocks[" in curve.data_path
                )

    # Several source slots can carry a curve with the same path. Copy it once.
    unique_curves = {}
    for curve in curves:
        unique_curves.setdefault((curve.data_path, curve.array_index), curve)
    return list(unique_curves.values())


def _copy_legacy_curve(source_curve, target_action):
    target_curve = target_action.fcurves.new(
        data_path=source_curve.data_path,
        index=source_curve.array_index,
    )
    for source_point in source_curve.keyframe_points:
        target_point = target_curve.keyframe_points.insert(
            frame=source_point.co[0],
            value=source_point.co[1],
        )
        target_point.interpolation = source_point.interpolation
        target_point.handle_left = source_point.handle_left
        target_point.handle_right = source_point.handle_right
        target_point.handle_left_type = source_point.handle_left_type
        target_point.handle_right_type = source_point.handle_right_type
        for attribute in (
            "easing",
            "amplitude",
            "back",
            "period",
            "type",
        ):
            if hasattr(source_point, attribute) and hasattr(target_point, attribute):
                try:
                    setattr(target_point, attribute, getattr(source_point, attribute))
                except (AttributeError, TypeError, ValueError):
                    pass
    target_curve.update()
    return target_curve


def _remove_matching_curve(curves, data_path, array_index):
    for curve in tuple(curves):
        if curve.data_path == data_path and curve.array_index == array_index:
            curves.remove(curve)


def _ensure_shape_key_slot(action, shape_keys, current_slot=None, avoid_slot_handles=()):
    """Return a KEY slot for the target, creating one when the Action lacks it."""
    slots = _shape_key_slots(action)
    if current_slot and getattr(current_slot, "target_id_type", None) == "KEY":
        return current_slot

    slot = next(
        (candidate for candidate in slots if candidate.handle not in avoid_slot_handles),
        None,
    )
    if slot is None:
        slot = action.slots.new(id_type="KEY", name=shape_keys.name)
    return slot


def _target_channelbag(action, shape_keys, animation_data, avoid_slot_handles=()):
    current_slot = (
        getattr(animation_data, "action_slot", None)
        if animation_data.action == action
        else None
    )
    slot = _ensure_shape_key_slot(
        action,
        shape_keys,
        current_slot=current_slot,
        avoid_slot_handles=avoid_slot_handles,
    )

    animation_data.action = action
    animation_data.action_slot = slot

    layer = action.layers[0] if action.layers else action.layers.new("Layer")
    strip = layer.strips[0] if layer.strips else layer.strips.new(type="KEYFRAME")
    channelbag = next(
        (
            candidate
            for candidate in strip.channelbags
            if candidate.slot_handle == slot.handle
        ),
        None,
    )
    if channelbag is None:
        channelbag = strip.channelbags.new(slot=slot)
    return channelbag


class ST_OT_transfer_shape_key_animation(Operator):
    bl_idname = "script_toolkit.transfer_shape_key_animation"
    bl_label = "Transfer Shape Key Animation"
    bl_description = (
        "Copy matching Shape Key animation curves to another Action without changing bone curves"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.st_transfer_shape_key_animation
        source_obj = props.source_mesh
        target_obj = props.target_mesh
        source_action = props.source_action
        target_action = props.target_action

        if not source_obj or not target_obj or not source_action or not target_action:
            self.report(
                {"ERROR"},
                "Choose a source mesh, source Action, target mesh, and target Action.",
            )
            return {"CANCELLED"}

        source_keys = _shape_keys(source_obj)
        target_keys = _shape_keys(target_obj)
        if source_keys is None:
            self.report({"ERROR"}, f"Source mesh '{source_obj.name}' has no Shape Keys.")
            return {"CANCELLED"}
        if target_keys is None:
            self.report({"ERROR"}, f"Target mesh '{target_obj.name}' has no Shape Keys.")
            return {"CANCELLED"}

        source_curves = _source_fcurves(source_action, source_keys)
        if not source_curves:
            self.report(
                {"WARNING"},
                f"No Shape Key animation curves found in '{source_action.name}'.",
            )
            return {"CANCELLED"}

        transferable_curves = []
        for curve in source_curves:
            key_name = _key_block_name(curve.data_path)
            if key_name is not None and key_name in target_keys.key_blocks:
                transferable_curves.append(curve)

        if not transferable_curves:
            self.report(
                {"WARNING"},
                "No animated Shape Keys from the source exist on the target mesh.",
            )
            return {"CANCELLED"}

        if _is_layered_action(target_action):
            animation_data = target_keys.animation_data
            if animation_data is None:
                animation_data = target_keys.animation_data_create()
            source_slot = _assigned_slot(source_keys, source_action)
            target_slot = (
                getattr(animation_data, "action_slot", None)
                if animation_data.action == target_action
                else None
            )
            if source_action == target_action:
                if (
                    source_slot
                    and target_slot
                    and source_slot.handle == target_slot.handle
                ):
                    self.report({"INFO"}, "Source and target already use the same Action slot.")
                    return {"FINISHED"}
                if source_slot and target_slot is None:
                    animation_data.action = target_action
                    animation_data.action_slot = source_slot
                    self.report(
                        {"INFO"},
                        "Assigned the source Action slot to the target Shape Keys.",
                    )
                    return {"FINISHED"}

            avoid_slot_handles = ()
            if source_action == target_action and source_slot is None and target_slot is None:
                avoid_slot_handles = tuple(slot.handle for slot in _shape_key_slots(source_action))
            channelbag = _target_channelbag(
                target_action,
                target_keys,
                animation_data,
                avoid_slot_handles=avoid_slot_handles,
            )
            target_curves = channelbag.fcurves
            for source_curve in transferable_curves:
                _remove_matching_curve(
                    target_curves,
                    source_curve.data_path,
                    source_curve.array_index,
                )
                target_curves.new_from_fcurve(source_curve)
        else:
            animation_data = target_keys.animation_data
            if animation_data is None:
                animation_data = target_keys.animation_data_create()
            if source_action == target_action:
                animation_data.action = target_action
                self.report(
                    {"INFO"},
                    "Assigned the same Action to the target Shape Keys; no curve copy was needed.",
                )
                return {"FINISHED"}
            animation_data.action = target_action
            target_curves = target_action.fcurves
            for source_curve in transferable_curves:
                _remove_matching_curve(
                    target_curves,
                    source_curve.data_path,
                    source_curve.array_index,
                )
                _copy_legacy_curve(source_curve, target_action)

        self.report(
            {"INFO"},
            f"Transferred {len(transferable_curves)} Shape Key animation curve(s) "
            f"from '{source_action.name}' to '{target_action.name}'.",
        )
        return {"FINISHED"}


def draw_ui(layout, context):
    props = context.scene.st_transfer_shape_key_animation

    source_box = layout.box()
    source_box.label(text="Source", icon="EXPORT")
    source_box.prop(props, "source_mesh", text="Mesh")
    source_box.prop(props, "source_action", text="Action")

    target_box = layout.box()
    target_box.label(text="Target", icon="IMPORT")
    target_box.prop(props, "target_mesh", text="Mesh")
    target_box.prop(props, "target_action", text="Action")

    row = layout.row()
    row.scale_y = 1.4
    row.operator(
        ST_OT_transfer_shape_key_animation.bl_idname,
        text="Transfer Shape Key Animation",
        icon="SHAPEKEY_DATA",
    )


classes = (
    ST_TransferShapeKeyAnimationProperties,
    ST_OT_transfer_shape_key_animation,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.st_transfer_shape_key_animation = PointerProperty(
        type=ST_TransferShapeKeyAnimationProperties
    )


def unregister():
    if hasattr(bpy.types.Scene, "st_transfer_shape_key_animation"):
        del bpy.types.Scene.st_transfer_shape_key_animation
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
