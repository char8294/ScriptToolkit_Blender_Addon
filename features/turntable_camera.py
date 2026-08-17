"""Turntable Camera tool integrated into Script Toolkit."""

import bpy
import math
import os
import re
import json
from mathutils import Vector
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    PointerProperty,
    IntProperty,
    CollectionProperty,
    StringProperty,
)
from bpy.types import Operator, PropertyGroup, UIList


MODEL_GRID_LABEL_COLLECTION_NAME = "Turntable_Model_Grid_Labels"
MODEL_GRID_LABEL_PREFIX = "Turntable_Model_Label_"

# =====================================================================
#  Preset Data
# =====================================================================

PRESETS = {
    'PIXEL_ART': {
        'name': "Pixel Art / Retro",
        'fps': 12,
        'frames': 60,
        'desc': "กระตุกดิบๆ สไตล์ Stop Motion ยุคคลาสสิก",
    },
    'PS1': {
        'name': "PS1 / ยุค 90s",
        'fps': 15,
        'frames': 75,
        'desc': "หน่วงๆ เฟรมต่ำแบบเกมคอนโซลยุคแรก",
    },
    'MODERN_30': {
        'name': "Modern Smooth 30fps",
        'fps': 30,
        'frames': 150,
        'desc': "ลื่นไหล เนียนตา 30fps",
    },
    'MODERN_60': {
        'name': "Modern Smooth 60fps",
        'fps': 60,
        'frames': 300,
        'desc': "ลื่นไหลสุด เนียนตา 60fps",
    },
}


# =====================================================================
#  Properties
# =====================================================================

def _filter_mesh_armature(self, obj):
    """Filter สำหรับ PointerProperty — แสดงเฉพาะ MESH, ARMATURE, EMPTY, CURVE, SURFACE, META, FONT"""
    return obj.type in {'MESH', 'ARMATURE', 'EMPTY', 'CURVE', 'SURFACE', 'META', 'FONT'}


def _filter_camera(self, obj):
    """Filter สำหรับ PointerProperty — แสดงเฉพาะ CAMERA"""
    return obj.type == 'CAMERA'


def _sync_preset_values(props):
    """Update custom FPS / frames from the selected preset."""
    p = PRESETS.get(props.preset)
    if p:
        props.custom_fps = p['fps']
        props.custom_frames = p['frames']


def _on_preset_update(self, context):
    _sync_preset_values(self)


# ── Rotation List Item ──

class TURNTABLE_RotationItem(PropertyGroup):
    """Item ในลิสต์สำหรับ Model Rotate — เป็นได้ทั้ง Object หรือ Collection"""
    item_type: EnumProperty(
        name="Type",
        items=[
            ('OBJECT', "Object", "หมุน Object เดี่ยว"),
            ('COLLECTION', "Collection", "หมุนทั้ง Collection เป็นกลุ่ม"),
        ],
        default='OBJECT',
    )

    target_object: PointerProperty(
        type=bpy.types.Object,
        name="Object",
        description="Object ที่จะหมุน",
    )

    collection_name: StringProperty(
        name="Collection",
        description="ชื่อ Collection ที่จะหมุน",
        default="",
    )


# ── Main Properties ──

class TURNTABLE_Properties(PropertyGroup):
    mode: EnumProperty(
        name="Mode",
        description="เลือกโหมดการหมุน",
        items=[
            ('CAMERA_ROTATE', "Camera Rotate", "กล้องหมุนรอบโมเดล (โมเดลเดียว)"),
            ('MODEL_ROTATE', "Model Rotate", "โมเดลหมุน กล้องอยู่กับที่ (โมเดลเยอะ)"),
        ],
        default='CAMERA_ROTATE',
    )

    preset: EnumProperty(
        name="Preset",
        description="เลือกพรีเซ็ตกล้อง",
        items=[
            ('PIXEL_ART', "Pixel Art / Retro", "12 fps · 60 frames — กระตุกดิบๆ Stop Motion"),
            ('PS1',       "PS1 / ยุค 90s",     "15 fps · 75 frames — หน่วงๆ คอนโซลยุคแรก"),
            ('MODERN_30', "Modern 30fps",      "30 fps · 150 frames — ลื่นไหล"),
            ('MODERN_60', "Modern 60fps",      "60 fps · 300 frames — ลื่นไหลสุด"),
        ],
        default='MODERN_30',
        update=_on_preset_update,
    )

    # ── Camera Orbit Settings ──

    target_object: PointerProperty(
        type=bpy.types.Object,
        name="Target",
        description="โมเดลที่กล้องจะหมุนรอบ",
        poll=_filter_mesh_armature,
    )

    camera_object: PointerProperty(
        type=bpy.types.Object,
        name="Camera",
        description="กล้องที่จะใช้ (ถ้าไม่เลือกจะสร้างใหม่)",
        poll=_filter_camera,
    )

    cam_distance: FloatProperty(
        name="Distance",
        description="ระยะห่างกล้องจากโมเดล",
        default=5.0,
        min=0.1,
        max=1000.0,
        unit='LENGTH',
    )

    cam_height: FloatProperty(
        name="Height",
        description="ความสูงกล้อง (จากจุดศูนย์กลางของ target)",
        default=1.5,
        min=-100.0,
        max=100.0,
        unit='LENGTH',
    )

    cam_tilt_x: FloatProperty(
        name="Tilt (X)",
        description="มุมก้ม/เงย ของกล้อง (มองขึ้น-ลง)",
        default=0.0,
        min=math.radians(-90),
        max=math.radians(90),
        subtype='ANGLE',
    )

    # ── Custom overrides ──

    custom_fps: IntProperty(
        name="FPS",
        description="Frame rate (จาก preset)",
        default=30,
        min=1,
        max=120,
    )

    custom_frames: IntProperty(
        name="Frames",
        description="จำนวนเฟรมที่หมุน 1 รอบ (จาก preset)",
        default=150,
        min=1,
        max=9999,
    )

    # ── Model Rotate List ──

    rotation_items: CollectionProperty(type=TURNTABLE_RotationItem)

    rotation_items_index: IntProperty(
        name="Active Rotation Item",
        default=0,
    )

    add_mode: EnumProperty(
        name="Add Mode",
        items=[
            ('OBJECT', "Object", "เพิ่ม selected objects เข้าลิสต์"),
            ('COLLECTION', "Collection", "เพิ่ม collections ของ selected objects เข้าลิสต์"),
        ],
        default='OBJECT',
    )

    model_rotation_style: EnumProperty(
        name="Rotation Style",
        description="Animation style for Camera Rotate and Model Rotate",
        items=[
            ('FULL_360', "Full 360 Turntable", "Rotate one full 360 degree loop"),
            ('BACK_FORTH', "Back-Forth Loop", "Swing between two angles and loop"),
        ],
        default='FULL_360',
    )

    model_back_forth_from: FloatProperty(
        name="From",
        description="Starting angle for Back-Forth Loop",
        default=math.radians(-22.0),
        min=math.radians(-360.0),
        max=math.radians(360.0),
        subtype='ANGLE',
    )

    model_back_forth_to: FloatProperty(
        name="To",
        description="Ending angle for Back-Forth Loop",
        default=math.radians(22.0),
        min=math.radians(-360.0),
        max=math.radians(360.0),
        subtype='ANGLE',
    )

    model_back_forth_ease: BoolProperty(
        name="Ease In/Out",
        description="Use Bezier interpolation for Back-Forth Loop keyframes",
        default=False,
    )

    model_grid_columns: IntProperty(
        name="Columns",
        description="Number of columns for Model Rotate grid layout",
        default=5,
        min=1,
        max=100,
    )

    model_grid_spacing: FloatProperty(
        name="Spacing",
        description="Gap between model bounding boxes in Blender units",
        default=1.0,
        min=0.0,
        soft_max=10.0,
        step=50,
        precision=2,
    )

    model_grid_layout_type: EnumProperty(
        name="Type",
        description="How item numbers advance inside the grid",
        items=[
            ('TYPE1', "Type 1 - Rightward Rows", "Next number moves to the right on +X"),
            ('TYPE2', "Type 2 - Legacy Direction", "Use the original axis-based numbering direction"),
        ],
        default='TYPE1',
    )

    model_grid_primary_axis: EnumProperty(
        name="Axis",
        description="Plane or direction used for the grid layout",
        items=[
            ('HORIZONTAL_X', "Ground Grid", "Rows go right on +X, then continue on +Y"),
            ('VERTICAL_NZ', "Vertical Stack", "Rows go right on +X, then continue downward on -Z"),
        ],
        default='HORIZONTAL_X',
    )

    model_label_show_name: bpy.props.BoolProperty(
        name="Create Model Name",
        description="Display the object or collection name above each grid item",
        default=False,
    )

    model_label_show_number: bpy.props.BoolProperty(
        name="Create Model Number",
        description="Display the sequence number above each grid item",
        default=False,
    )

    model_label_settings_expanded: bpy.props.BoolProperty(
        name="Create Label",
        description="Show grid label creation settings",
        default=False,
    )

    model_label_font_size: FloatProperty(
        name="Font Size",
        description="Size of grid labels in Blender units",
        default=0.35,
        min=0.01,
        soft_max=5.0,
        step=10,
        precision=2,
    )

    model_label_gap: FloatProperty(
        name="Label Gap",
        description="Distance between the label and the model bounding box",
        default=0.20,
        min=0.0,
        soft_max=10.0,
        step=10,
        precision=2,
    )

    model_label_position: EnumProperty(
        name="Position",
        description="Place grid labels above or below each model",
        items=[
            ('TOP', "Top", "Place labels above the model"),
            ('BOTTOM', "Bottom", "Place labels below the model"),
        ],
        default='TOP',
    )

    show_camera_settings: bpy.props.BoolProperty(
        name="Show Camera Settings",
        default=True,
        description="พับ/กาง การตั้งค่าระยะและมุมกล้อง",
    )


# =====================================================================
#  Helper Functions
# =====================================================================

def _set_fcurve_interpolation(obj, data_path, index, interpolation):
    """Set F-Curve interpolation while supporting Blender 5.0+ and older actions."""
    if not obj.animation_data or not obj.animation_data.action:
        return

    action = obj.animation_data.action

    # 1. Blender 5.0+ Slotted Actions API
    if hasattr(action, "fcurve_ensure_for_datablock"):
        try:
            fc = action.fcurve_ensure_for_datablock(
                datablock=obj,
                data_path=data_path,
                index=index,
            )
            if fc:
                for kp in fc.keyframe_points:
                    kp.interpolation = interpolation
                return
        except Exception:
            pass

    # 2. Blender 4.x/3.x Legacy API
    if hasattr(action, "fcurves"):
        for fc in action.fcurves:
            if fc.data_path == data_path and fc.array_index == index:
                for kp in fc.keyframe_points:
                    kp.interpolation = interpolation


def _set_fcurve_linear_interpolation(obj, data_path, index):
    """ตั้งค่า interpolation ของ F-Curve ที่กำหนดให้เป็น LINEAR โดยรองรับทั้ง Blender 5.0+ และเวอร์ชันเก่า"""
    if not obj.animation_data or not obj.animation_data.action:
        return

    action = obj.animation_data.action

    # 1. Blender 5.0+ Slotted Actions API
    if hasattr(action, "fcurve_ensure_for_datablock"):
        try:
            fc = action.fcurve_ensure_for_datablock(datablock=obj, data_path=data_path, index=index)
            if fc:
                for kp in fc.keyframe_points:
                    kp.interpolation = 'LINEAR'
                return
        except Exception:
            pass

    # 2. Blender 4.x/3.x Legacy API
    if hasattr(action, "fcurves"):
        for fc in action.fcurves:
            if fc.data_path == data_path and fc.array_index == index:
                for kp in fc.keyframe_points:
                    kp.interpolation = 'LINEAR'


def _remove_fcurve(obj, data_path, index):
    """ลบ F-Curve สำหรับ property และ index ที่ระบุ โดยรองรับทั้ง Blender 5.0+ และเวอร์ชันเก่า"""
    if not obj.animation_data or not obj.animation_data.action:
        return False

    action = obj.animation_data.action
    removed = False

    # 1. Blender 5.0+ Slotted Actions API
    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                try:
                    cb = strip.channelbag(obj.animation_data.action_slot, ensure=False)
                    if cb:
                        to_remove = [fc for fc in cb.fcurves if fc.data_path == data_path and fc.array_index == index]
                        for fc in to_remove:
                            cb.fcurves.remove(fc)
                            removed = True
                except Exception:
                    pass

    # 2. Blender 4.x/3.x Legacy API
    if hasattr(action, "fcurves"):
        to_remove = [fc for fc in action.fcurves if fc.data_path == data_path and fc.array_index == index]
        for fc in to_remove:
            action.fcurves.remove(fc)
            removed = True

    return removed


def _clean_empty_action(obj):
    """ตรวจสอบและลบ Action ที่ไม่มี F-Curves เพื่อประหยัดพื้นที่"""
    if not obj.animation_data or not obj.animation_data.action:
        return

    action = obj.animation_data.action
    has_fcurves = False

    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                try:
                    cb = strip.channelbag(obj.animation_data.action_slot, ensure=False)
                    if cb and len(cb.fcurves) > 0:
                        has_fcurves = True
                        break
                except Exception:
                    pass
            if has_fcurves:
                break
    elif hasattr(action, "fcurves"):
        if len(action.fcurves) > 0:
            has_fcurves = True

    if not has_fcurves:
        try:
            bpy.data.actions.remove(action)
        except Exception:
            pass
        obj.animation_data_clear()


def _get_or_create_camera(context, props):
    """คืน camera object — ใช้ตัวที่เลือก หรือสร้างใหม่"""
    if props.camera_object and props.camera_object.type == 'CAMERA':
        return props.camera_object

    cam_data = bpy.data.cameras.new("TurntableCam")
    cam_obj = bpy.data.objects.new("TurntableCam", cam_data)
    context.collection.objects.link(cam_obj)
    props.camera_object = cam_obj
    return cam_obj


def _find_turntable_empty(cam_obj):
    """หา Empty ที่เป็น turntable pivot ของกล้อง (ถ้ามี)"""
    if cam_obj.parent and cam_obj.parent.type == 'EMPTY':
        if cam_obj.parent.name.startswith("Turntable_Pivot"):
            return cam_obj.parent
    return None


def _clear_track_to(cam_obj):
    """ลบ Track To constraint ที่ชื่อ TurntableTrack (ถ้ามี)"""
    to_remove = [c for c in cam_obj.constraints if c.name == "TurntableTrack"]
    for c in to_remove:
        cam_obj.constraints.remove(c)


def _is_model_grid_label(obj):
    """Return True for labels generated by the Model Rotate grid tool."""
    return obj.name.startswith(MODEL_GRID_LABEL_PREFIX)


def _make_bbox(min_v, max_v):
    center = (min_v + max_v) * 0.5
    return {
        'min': min_v,
        'max': max_v,
        'center': center,
        'dims': max_v - min_v,
    }


def _get_object_world_bbox(obj):
    """Return world-space bbox data for an object, with a location fallback."""
    if obj is None or _is_model_grid_label(obj):
        return None

    try:
        local_corners = [Vector(corner) for corner in obj.bound_box]
    except Exception:
        local_corners = []

    bbox_unavailable = (
        not local_corners or
        all(
            abs(value + 1.0) < 0.000001
            for corner in local_corners
            for value in corner
        )
    )

    if bbox_unavailable:
        loc = obj.matrix_world.translation.copy()
        return _make_bbox(loc.copy(), loc.copy())

    world_corners = [obj.matrix_world @ corner for corner in local_corners]
    min_v = Vector((
        min(c.x for c in world_corners),
        min(c.y for c in world_corners),
        min(c.z for c in world_corners),
    ))
    max_v = Vector((
        max(c.x for c in world_corners),
        max(c.y for c in world_corners),
        max(c.z for c in world_corners),
    ))
    return _make_bbox(min_v, max_v)


def _combine_bboxes(bboxes):
    valid = [bbox for bbox in bboxes if bbox is not None]
    if not valid:
        return None

    min_v = Vector((
        min(bbox['min'].x for bbox in valid),
        min(bbox['min'].y for bbox in valid),
        min(bbox['min'].z for bbox in valid),
    ))
    max_v = Vector((
        max(bbox['max'].x for bbox in valid),
        max(bbox['max'].y for bbox in valid),
        max(bbox['max'].z for bbox in valid),
    ))
    return _make_bbox(min_v, max_v)


def _get_collection_world_bbox(collection):
    """Return aggregate bbox for a collection, excluding generated helpers."""
    if collection is None:
        return None

    bboxes = []
    for obj in collection.all_objects:
        if _is_model_grid_label(obj):
            continue
        if obj.name.startswith("Turntable_Rot_"):
            continue
        bboxes.append(_get_object_world_bbox(obj))

    return _combine_bboxes(bboxes)


def _get_collection_move_roots(collection):
    """Return root objects to move a collection without breaking hierarchy."""
    if collection is None:
        return []

    objects = [
        obj for obj in collection.all_objects
        if not _is_model_grid_label(obj)
    ]
    object_set = set(objects)
    return [obj for obj in objects if obj.parent not in object_set]


def _move_objects_by_delta(objects, delta):
    for obj in objects:
        matrix = obj.matrix_world.copy()
        matrix.translation = matrix.translation + delta
        obj.matrix_world = matrix


def _get_rotation_item_grid_data(item):
    if item.item_type == 'OBJECT':
        obj = item.target_object
        if obj is None or obj.type == 'CAMERA':
            return None

        bbox = _get_object_world_bbox(obj)
        if bbox is None:
            return None

        return {
            'name': obj.name,
            'bbox': bbox,
            'move_roots': [obj],
            'item_type': 'OBJECT',
        }

    collection = bpy.data.collections.get(item.collection_name)
    if collection is None:
        return None

    bbox = _get_collection_world_bbox(collection)
    move_roots = _get_collection_move_roots(collection)
    if bbox is None or not move_roots:
        return None

    return {
        'name': collection.name,
        'bbox': bbox,
        'move_roots': move_roots,
        'item_type': 'COLLECTION',
    }


def _get_model_grid_offset(index, columns, cell_col, cell_row,
                           axis, layout_type):
    col_idx = index % columns
    row_idx = index // columns

    if layout_type == 'TYPE2' and axis == 'VERTICAL_NZ':
        primary = Vector((0.0, 0.0, -1.0))
        secondary = Vector((1.0, 0.0, 0.0))
    elif layout_type == 'TYPE2':
        primary = Vector((0.0, 1.0, 0.0))
        secondary = Vector((1.0, 0.0, 0.0))
    elif axis == 'VERTICAL_NZ':
        primary = Vector((1.0, 0.0, 0.0))
        secondary = Vector((0.0, 0.0, -1.0))
    else:
        primary = Vector((1.0, 0.0, 0.0))
        secondary = Vector((0.0, 1.0, 0.0))

    return (
        primary * col_idx * cell_col +
        secondary * row_idx * cell_row
    )


def _get_or_create_model_grid_label_collection(context):
    collection = bpy.data.collections.get(MODEL_GRID_LABEL_COLLECTION_NAME)
    if collection is None:
        collection = bpy.data.collections.new(MODEL_GRID_LABEL_COLLECTION_NAME)
        context.scene.collection.children.link(collection)
    elif collection.name not in {
        child.name for child in context.scene.collection.children
    }:
        context.scene.collection.children.link(collection)
    return collection


def _clear_model_grid_labels():
    collection = bpy.data.collections.get(MODEL_GRID_LABEL_COLLECTION_NAME)
    if collection is None:
        return 0

    curves = []
    removed = 0
    for obj in list(collection.objects):
        if not obj.name.startswith(MODEL_GRID_LABEL_PREFIX):
            continue
        if obj.type == 'FONT' and obj.data is not None:
            curves.append(obj.data)
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1

    for curve in curves:
        if curve.users == 0:
            bpy.data.curves.remove(curve)

    return removed


def _create_model_grid_label(collection, index, item_name, props, bbox):
    parts = []
    if props.model_label_show_number:
        parts.append(f"#{index + 1}")
    if props.model_label_show_name:
        parts.append(item_name)

    if not parts:
        return None

    body = " - ".join(parts)
    safe_name = bpy.path.clean_name(item_name)
    text_data = bpy.data.curves.new(
        name=f"{MODEL_GRID_LABEL_PREFIX}{index + 1:03d}_{safe_name}",
        type='FONT',
    )
    text_data.body = body
    text_data.align_x = 'CENTER'
    text_data.align_y = (
        'BOTTOM' if props.model_label_position == 'TOP' else 'TOP'
    )
    text_data.size = props.model_label_font_size

    text_obj = bpy.data.objects.new(
        name=f"{MODEL_GRID_LABEL_PREFIX}{index + 1:03d}_{safe_name}",
        object_data=text_data,
    )
    collection.objects.link(text_obj)

    center = bbox['center']
    if props.model_label_position == 'TOP':
        label_z = bbox['max'].z + props.model_label_gap
    else:
        label_z = bbox['min'].z - props.model_label_gap

    text_obj.rotation_euler = (math.pi / 2, 0, 0)
    text_obj.location = Vector((center.x, center.y, label_z))
    return text_obj


def _get_collection_center(collection):
    """คำนวณจุดศูนย์กลางของทุก object ใน Collection (รวม sub-collections)"""
    positions = []
    for obj in collection.all_objects:
        positions.append(obj.matrix_world.translation.copy())
    if not positions:
        return Vector((0, 0, 0))
    center = Vector((0, 0, 0))
    for p in positions:
        center += p
    center /= len(positions)
    return center


def _find_rotation_pivot(obj):
    """หา Turntable_Rot_Pivot ที่เป็น parent ของ object (ถ้ามี)"""
    if obj.parent and obj.parent.type == 'EMPTY':
        if obj.parent.name.startswith("Turntable_Rot_"):
            return obj.parent
    return None


def _rotate_single_object(obj, total_frames):
    """ใส่ keyframe rotation Z 360° ให้ object เดี่ยว"""
    _remove_fcurve(obj, "rotation_euler", 2)

    original_z = obj.rotation_euler.z
    obj.rotation_euler.z = original_z
    obj.keyframe_insert(data_path="rotation_euler", index=2, frame=1)

    obj.rotation_euler.z = original_z + math.radians(360)
    obj.keyframe_insert(data_path="rotation_euler", index=2, frame=total_frames + 1)

    _set_fcurve_linear_interpolation(obj, "rotation_euler", 2)


def _get_back_forth_middle_frame(total_frames):
    return max(2, int(round((total_frames + 2) * 0.5)))


def _rotate_single_object_back_forth(obj, total_frames, angle_from, angle_to,
                                     use_ease=False):
    """ใส่ keyframe rotation Z แบบแกว่งไปกลับให้ object เดี่ยว"""
    _remove_fcurve(obj, "rotation_euler", 2)

    middle_frame = _get_back_forth_middle_frame(total_frames)
    end_frame = total_frames + 1

    obj.rotation_euler.z = angle_from
    obj.keyframe_insert(data_path="rotation_euler", index=2, frame=1)

    obj.rotation_euler.z = angle_to
    obj.keyframe_insert(data_path="rotation_euler", index=2, frame=middle_frame)

    obj.rotation_euler.z = angle_from
    obj.keyframe_insert(data_path="rotation_euler", index=2, frame=end_frame)

    interpolation = 'BEZIER' if use_ease else 'LINEAR'
    _set_fcurve_interpolation(obj, "rotation_euler", 2, interpolation)


def _prepare_collection_rotation_pivot(context, collection):
    """สร้าง Empty Pivot ตรงกลาง Collection แล้ว Parent ทุก object → Pivot"""
    # ── สร้างหรือใช้ Pivot Empty เดิม ──
    pivot_name = f"Turntable_Rot_{collection.name}"
    pivot = bpy.data.objects.get(pivot_name)

    if pivot is None:
        pivot = bpy.data.objects.new(pivot_name, None)
        pivot.empty_display_type = 'PLAIN_AXES'
        pivot.empty_display_size = 0.5
        # Link pivot เข้า collection ของมันเอง (ไม่ใช่ context.collection)
        collection.objects.link(pivot)

    # ── คำนวณ center จาก direct objects เท่านั้น (ไม่รวม pivot) ──
    positions = []
    for obj in collection.objects:
        if obj == pivot:
            continue
        positions.append(obj.matrix_world.translation.copy())
    if positions:
        center = Vector((0, 0, 0))
        for p in positions:
            center += p
        center /= len(positions)
    else:
        center = Vector((0, 0, 0))

    pivot.location = center
    pivot.rotation_euler = (0, 0, 0)

    # ── อัปเดต depsgraph เพื่อให้ matrix_world ของ pivot ถูกต้อง ──
    context.view_layer.update()

    # ── Parent เฉพาะ direct objects ของ collection นี้ → Pivot ──
    for obj in collection.objects:
        if obj == pivot:
            continue
        # ข้าม object ที่ถูก parent ไปยัง pivot ของ collection อื่นแล้ว
        if obj.parent is not None and obj.parent.name.startswith("Turntable_Rot_"):
            continue
        if obj.parent is None:
            # เก็บ world matrix เดิมของ object
            orig_matrix = obj.matrix_world.copy()
            obj.parent = pivot
            obj.matrix_parent_inverse = pivot.matrix_world.inverted()
            # คืนค่า world matrix เดิมเพื่อให้ object ไม่เคลื่อนที่
            obj.matrix_world = orig_matrix

    # ── ลบ animation เก่าของ Pivot ──
    if pivot.animation_data and pivot.animation_data.action:
        try:
            bpy.data.actions.remove(pivot.animation_data.action)
        except Exception:
            pass
        pivot.animation_data_clear()

    return pivot


def _rotate_collection_as_group(context, collection, total_frames):
    """ใส่ keyframe rotation Z 360° ให้ collection เป็นกลุ่ม"""
    pivot = _prepare_collection_rotation_pivot(context, collection)

    pivot.rotation_euler = (0, 0, 0)
    pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=1)

    pivot.rotation_euler.z = math.radians(360)
    pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=total_frames + 1)

    _set_fcurve_linear_interpolation(pivot, "rotation_euler", 2)
    return pivot


def _rotate_collection_back_forth(context, collection, total_frames,
                                  angle_from, angle_to, use_ease=False):
    """ใส่ keyframe rotation Z แบบแกว่งไปกลับให้ collection เป็นกลุ่ม"""
    pivot = _prepare_collection_rotation_pivot(context, collection)
    middle_frame = _get_back_forth_middle_frame(total_frames)
    end_frame = total_frames + 1

    pivot.rotation_euler = (0, 0, angle_from)
    pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=1)

    pivot.rotation_euler.z = angle_to
    pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=middle_frame)

    pivot.rotation_euler.z = angle_from
    pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=end_frame)

    interpolation = 'BEZIER' if use_ease else 'LINEAR'
    _set_fcurve_interpolation(pivot, "rotation_euler", 2, interpolation)
    return pivot


def _clear_single_object_animation(obj):
    """ลบ rotation Z animation ของ object เดี่ยว"""
    if _remove_fcurve(obj, "rotation_euler", 2):
        _clean_empty_action(obj)
        return True
    return False


def _clear_collection_animation(collection):
    """ลบ Pivot Empty + unparent objects ของ collection"""
    pivot_name = f"Turntable_Rot_{collection.name}"
    pivot = bpy.data.objects.get(pivot_name)
    if pivot is None:
        return False

    # Unparent ทุก child
    for child in list(pivot.children):
        child_world = child.matrix_world.copy()
        child.parent = None
        child.matrix_world = child_world

    # ลบ Pivot
    bpy.data.objects.remove(pivot, do_unlink=True)
    return True


# =====================================================================
#  UIList
# =====================================================================

class TURNTABLE_UL_rotation_items(UIList):
    """UIList สำหรับแสดง Rotation Items (แบบรายชื่อ อ่านอย่างเดียว)"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.label(text=f"{index + 1}.")

            if item.item_type == 'OBJECT':
                obj = item.target_object
                if obj:
                    row.label(text=obj.name, icon='OBJECT_DATA')
                else:
                    row.label(text="(ไม่ได้เลือก Object)", icon='ERROR')
                row.label(text="Object", icon='BLANK1')
            else:
                col = bpy.data.collections.get(item.collection_name)
                if col:
                    row.label(text=col.name, icon='OUTLINER_COLLECTION')
                else:
                    row.label(text=item.collection_name or "(ไม่พบ Collection)", icon='ERROR')
                row.label(text="Collection", icon='BLANK1')

        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon='OBJECT_DATA')


# =====================================================================
#  Operators
# =====================================================================

# ─────────────────────────────────────────────────────────────────────
#  Rotation List — Add / Remove Items
# ─────────────────────────────────────────────────────────────────────

class TURNTABLE_OT_add_selected(Operator):
    """เพิ่ม selected objects หรือ collections เข้าลิสต์ตาม Add Mode"""
    bl_idname = "turntable.add_selected"
    bl_label = "Add Selected"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = getattr(context.scene, "turntable_props", None)
        if props and props.add_mode == 'COLLECTION':
            return True
        return len(context.selected_objects) > 0

    def execute(self, context):
        props = context.scene.turntable_props
        added = 0
        skipped = 0

        if props.add_mode == 'OBJECT':
            # ── Bulk add selected objects ──
            for obj in context.selected_objects:
                if obj.type == 'CAMERA':
                    continue
                # ตรวจซ้ำ
                already = any(
                    i.item_type == 'OBJECT' and i.target_object == obj
                    for i in props.rotation_items
                )
                if already:
                    skipped += 1
                    continue
                new_item = props.rotation_items.add()
                new_item.item_type = 'OBJECT'
                new_item.target_object = obj
                added += 1

            label = "objects"

        else:  # COLLECTION
            col_names = set()
            
            if len(context.selected_objects) > 0:
                # ── หา collections ที่ selected objects อยู่ ──
                for obj in context.selected_objects:
                    if obj.type == 'CAMERA':
                        continue
                    for col in bpy.data.collections:
                        if obj.name in col.objects:
                            col_names.add(col.name)
            else:
                # ── ถ้าไม่ได้เลือก object ให้ใช้ active collection จาก Outliner ──
                if context.collection and context.collection.name in bpy.data.collections:
                    col_names.add(context.collection.name)

            for col_name in sorted(col_names):
                # ตรวจซ้ำ
                already = any(
                    i.item_type == 'COLLECTION' and i.collection_name == col_name
                    for i in props.rotation_items
                )
                if already:
                    skipped += 1
                    continue
                new_item = props.rotation_items.add()
                new_item.item_type = 'COLLECTION'
                new_item.collection_name = col_name
                added += 1

            label = "collections"

        # อัปเดต active index
        if len(props.rotation_items) > 0:
            props.rotation_items_index = len(props.rotation_items) - 1

        if added == 0 and skipped == 0:
            self.report({'WARNING'}, "ไม่มีอะไรให้เพิ่ม — เลือก objects ก่อน")
            return {'CANCELLED'}

        msg = f"เพิ่ม {added} {label}"
        if skipped > 0:
            msg += f" (ข้าม {skipped} ซ้ำ)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class TURNTABLE_OT_remove_rotation_item(Operator):
    """ลบ item ที่เลือกออกจากลิสต์"""
    bl_idname = "turntable.remove_rotation_item"
    bl_label = "Remove Item"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.turntable_props
        return len(props.rotation_items) > 0

    def execute(self, context):
        props = context.scene.turntable_props
        idx = props.rotation_items_index

        if idx < 0 or idx >= len(props.rotation_items):
            return {'CANCELLED'}

        item = props.rotation_items[idx]
        name = ""
        if item.item_type == 'OBJECT' and item.target_object:
            name = item.target_object.name
        elif item.item_type == 'COLLECTION':
            name = item.collection_name

        props.rotation_items.remove(idx)
        props.rotation_items_index = min(idx, len(props.rotation_items) - 1)

        self.report({'INFO'}, f"ลบ '{name}' ออกจากลิสต์")
        return {'FINISHED'}


class TURNTABLE_OT_move_rotation_item(Operator):
    """ขยับรายการขึ้น/ลง"""
    bl_idname = "turntable.move_rotation_item"
    bl_label = "Move Item"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        items=[('UP', "Up", ""), ('DOWN', "Down", "")]
    )

    @classmethod
    def poll(cls, context):
        props = context.scene.turntable_props
        return len(props.rotation_items) > 1

    def execute(self, context):
        props = context.scene.turntable_props
        idx = props.rotation_items_index
        items = props.rotation_items

        if self.direction == 'UP' and idx > 0:
            items.move(idx, idx - 1)
            props.rotation_items_index -= 1
        elif self.direction == 'DOWN' and idx < len(items) - 1:
            items.move(idx, idx + 1)
            props.rotation_items_index += 1

        return {'FINISHED'}


class TURNTABLE_OT_apply_model_grid(Operator):
    """Arrange Model Rotate items in a grid by moving the original objects."""
    bl_idname = "turntable.apply_model_grid"
    bl_label = "Apply Grid Layout"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = getattr(context.scene, "turntable_props", None)
        return (
            context.mode == 'OBJECT' and
            props is not None and
            len(props.rotation_items) > 0
        )

    def execute(self, context):
        props = context.scene.turntable_props
        _clear_model_grid_labels()

        entries = []
        for item in props.rotation_items:
            data = _get_rotation_item_grid_data(item)
            if data is not None:
                entries.append(data)

        if not entries:
            self.report({'WARNING'}, "No valid objects or collections to arrange.")
            return {'CANCELLED'}

        max_dx = max(entry['bbox']['dims'].x for entry in entries)
        max_dy = max(entry['bbox']['dims'].y for entry in entries)
        max_dz = max(entry['bbox']['dims'].z for entry in entries)
        spacing = props.model_grid_spacing
        axis = props.model_grid_primary_axis
        layout_type = props.model_grid_layout_type

        if layout_type == 'TYPE2' and axis == 'VERTICAL_NZ':
            cell_col = max_dz + spacing
            cell_row = max_dx + spacing
        elif layout_type == 'TYPE2':
            cell_col = max_dy + spacing
            cell_row = max_dx + spacing
        elif axis == 'VERTICAL_NZ':
            cell_col = max_dx + spacing
            cell_row = max_dz + spacing
        else:
            cell_col = max_dx + spacing
            cell_row = max_dy + spacing

        columns = min(props.model_grid_columns, len(entries))
        origin_center = entries[0]['bbox']['center'].copy()

        for index, entry in enumerate(entries):
            offset = _get_model_grid_offset(
                index, columns, cell_col, cell_row, axis, layout_type,
            )
            target_center = origin_center + offset
            delta = target_center - entry['bbox']['center']

            _move_objects_by_delta(entry['move_roots'], delta)
            entry['final_bbox'] = _make_bbox(
                entry['bbox']['min'] + delta,
                entry['bbox']['max'] + delta,
            )

        context.view_layer.update()

        labels_enabled = (
            props.model_label_show_name or
            props.model_label_show_number
        )
        if labels_enabled:
            label_collection = _get_or_create_model_grid_label_collection(
                context,
            )
            for index, entry in enumerate(entries):
                _create_model_grid_label(
                    label_collection,
                    index,
                    entry['name'],
                    props,
                    entry['final_bbox'],
                )

        rows = math.ceil(len(entries) / columns)
        self.report(
            {'INFO'},
            f"Applied grid layout to {len(entries)} item(s): "
            f"{columns} x {rows}",
        )
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────
#  Camera Orbit — Step 1: Create / Update Camera
# ─────────────────────────────────────────────────────────────────────

class TURNTABLE_OT_create_camera(Operator):
    """สร้าง/อัพเดทกล้อง — วางตำแหน่งตาม Distance, Height, Tilt(X) และชี้ไปที่ Target"""
    bl_idname = "turntable.create_camera"
    bl_label = "Create / Update Camera"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.turntable_props
        return props.target_object is not None

    def execute(self, context):
        props = context.scene.turntable_props
        target = props.target_object

        if target is None:
            self.report({'ERROR'}, "กรุณาเลือก Target Object ก่อน")
            return {'CANCELLED'}

        cam_obj = _get_or_create_camera(context, props)

        # ── ตำแหน่งกล้อง ──
        target_loc = target.matrix_world.translation.copy()
        cam_obj.location.x = target_loc.x
        cam_obj.location.y = target_loc.y - props.cam_distance
        cam_obj.location.z = target_loc.z + props.cam_height

        # ── ลบ constraint เดิม แล้วเพิ่มใหม่ ──
        _clear_track_to(cam_obj)
        track = cam_obj.constraints.new('TRACK_TO')
        track.name = "TurntableTrack"
        track.target = target
        track.track_axis = 'TRACK_NEGATIVE_Z'
        track.up_axis = 'UP_Y'

        # ── Tilt (X) — เพิ่มมุมก้ม/เงยเพิ่มเติม ──
        cam_obj.rotation_euler = (props.cam_tilt_x, 0, 0)

        # ── ตั้งเป็น active camera ──
        context.scene.camera = cam_obj

        self.report({'INFO'}, f"กล้อง '{cam_obj.name}' พร้อมแล้ว — ชี้ไปที่ '{target.name}'")
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────
#  Camera Orbit — Step 2: Start Turntable
# ─────────────────────────────────────────────────────────────────────

class TURNTABLE_OT_start_turntable(Operator):
    """ใส่ keyframe ให้กล้องหมุนรอบ Target Object 360°"""
    bl_idname = "turntable.start_turntable"
    bl_label = "Start Turntable"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.turntable_props
        return (props.target_object is not None and
                props.camera_object is not None)

    def execute(self, context):
        props = context.scene.turntable_props
        target = props.target_object
        cam_obj = props.camera_object
        scene = context.scene

        if target is None or cam_obj is None:
            self.report({'ERROR'}, "กรุณาสร้างกล้องก่อน (Create / Update Camera)")
            return {'CANCELLED'}

        total_frames = props.custom_frames
        fps = props.custom_fps
        rotation_style = props.model_rotation_style
        use_ease = props.model_back_forth_ease
        target_loc = target.matrix_world.translation.copy()

        # ── สร้างหรือใช้ Empty Pivot ──
        pivot = _find_turntable_empty(cam_obj)
        if pivot is None:
            pivot = bpy.data.objects.new("Turntable_Pivot", None)
            pivot.empty_display_type = 'PLAIN_AXES'
            pivot.empty_display_size = 0.5
            context.collection.objects.link(pivot)

        pivot.location = target_loc
        pivot.rotation_euler = (0, 0, 0)

        # ── Parent กล้อง → Pivot (keep transform) ──
        cam_world = cam_obj.matrix_world.copy()
        cam_obj.parent = pivot
        cam_obj.matrix_world = cam_world

        # ── ลบ animation เก่าของ Pivot (ถ้ามี) ──
        if pivot.animation_data and pivot.animation_data.action:
            try:
                bpy.data.actions.remove(pivot.animation_data.action)
            except Exception:
                pass
            pivot.animation_data_clear()

        if rotation_style == 'BACK_FORTH':
            middle_frame = _get_back_forth_middle_frame(total_frames)
            end_frame = total_frames + 1

            pivot.rotation_euler = (0, 0, props.model_back_forth_from)
            pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=1)

            pivot.rotation_euler.z = props.model_back_forth_to
            pivot.keyframe_insert(
                data_path="rotation_euler",
                index=2,
                frame=middle_frame,
            )

            pivot.rotation_euler.z = props.model_back_forth_from
            pivot.keyframe_insert(
                data_path="rotation_euler",
                index=2,
                frame=end_frame,
            )
        else:
            # ── Keyframe Pivot rotation Z ──
            pivot.rotation_euler = (0, 0, 0)
            pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=1)

            pivot.rotation_euler.z = math.radians(360)
            pivot.keyframe_insert(data_path="rotation_euler", index=2, frame=total_frames + 1)

        # ── ตั้ง interpolation = LINEAR ──
        interpolation = (
            'BEZIER'
            if rotation_style == 'BACK_FORTH' and use_ease
            else 'LINEAR'
        )
        _set_fcurve_interpolation(pivot, "rotation_euler", 2, interpolation)

        # ── ตั้งค่า Scene ──
        scene.render.fps = fps
        scene.frame_start = 1
        scene.frame_end = total_frames
        scene.frame_current = 1

        style_label = (
            "Back-Forth Loop"
            if rotation_style == 'BACK_FORTH'
            else "Full 360 Turntable"
        )
        self.report({'INFO'},
                    f"{style_label}: {total_frames} frames @ {fps} fps — "
                    f"กล้อง '{cam_obj.name}' หมุนรอบ '{target.name}'")
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────
#  Model Rotate — หมุนจากลิสต์ (Object + Collection)
# ─────────────────────────────────────────────────────────────────────

class TURNTABLE_OT_rotate_models(Operator):
    """หมุนทุก item ในลิสต์ — Object หมุนเดี่ยว, Collection หมุนเป็นกลุ่ม"""
    bl_idname = "turntable.rotate_models"
    bl_label = "Rotate All"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        props = context.scene.turntable_props
        return len(props.rotation_items) > 0

    def execute(self, context):
        props = context.scene.turntable_props
        scene = context.scene
        total_frames = props.custom_frames
        fps = props.custom_fps
        rotation_style = props.model_rotation_style
        angle_from = props.model_back_forth_from
        angle_to = props.model_back_forth_to
        use_ease = props.model_back_forth_ease
        obj_count = 0
        col_count = 0

        for item in props.rotation_items:
            if item.item_type == 'OBJECT':
                obj = item.target_object
                if obj is None:
                    continue
                if rotation_style == 'BACK_FORTH':
                    _rotate_single_object_back_forth(
                        obj, total_frames, angle_from, angle_to, use_ease,
                    )
                else:
                    _rotate_single_object(obj, total_frames)
                obj_count += 1

            elif item.item_type == 'COLLECTION':
                col = bpy.data.collections.get(item.collection_name)
                if col is None:
                    continue
                if rotation_style == 'BACK_FORTH':
                    _rotate_collection_back_forth(
                        context, col, total_frames, angle_from, angle_to,
                        use_ease,
                    )
                else:
                    _rotate_collection_as_group(context, col, total_frames)
                col_count += 1

        # ── ตั้งค่า Scene ──
        scene.render.fps = fps
        scene.frame_start = 1
        scene.frame_end = total_frames
        scene.frame_current = 1

        style_label = (
            "Back-Forth Loop"
            if rotation_style == 'BACK_FORTH'
            else "Full 360 Turntable"
        )
        self.report({'INFO'},
                    f"{style_label}: {obj_count} objects + {col_count} collections — "
                    f"{total_frames} frames @ {fps} fps")
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────
#  Clear Animation
# ─────────────────────────────────────────────────────────────────────

class TURNTABLE_OT_clear_animation(Operator):
    """ลบ turntable animation ที่สร้างไว้"""
    bl_idname = "turntable.clear_animation"
    bl_label = "Clear Animation"
    bl_options = {'REGISTER', 'UNDO'}

    clear_mode: EnumProperty(
        name="Clear Mode",
        items=[
            ('CAMERA', "Camera Rotate", "ลบ animation ของ Camera Rotate"),
            ('MODELS', "Model Rotate", "ลบ rotation animation ของลิสต์"),
        ],
        default='CAMERA',
    )

    def execute(self, context):
        props = context.scene.turntable_props

        if self.clear_mode == 'CAMERA':
            cam_obj = props.camera_object
            if cam_obj is None:
                self.report({'WARNING'}, "ไม่มีกล้องที่จะ clear")
                return {'CANCELLED'}

            # ── ลบ Pivot Empty + animation ──
            pivot = _find_turntable_empty(cam_obj)
            if pivot:
                cam_world = cam_obj.matrix_world.copy()
                cam_obj.parent = None
                cam_obj.matrix_world = cam_world
                bpy.data.objects.remove(pivot, do_unlink=True)

            # ── ลบ Track To constraint ──
            _clear_track_to(cam_obj)

            # ── ลบ animation ของกล้อง (ถ้ามี) ──
            if cam_obj.animation_data and cam_obj.animation_data.action:
                try:
                    bpy.data.actions.remove(cam_obj.animation_data.action)
                except Exception:
                    pass
                cam_obj.animation_data_clear()

            self.report({'INFO'}, f"ลบ Turntable animation ของกล้อง '{cam_obj.name}' แล้ว")

        else:  # MODELS
            obj_count = 0
            col_count = 0

            for item in props.rotation_items:
                if item.item_type == 'OBJECT':
                    obj = item.target_object
                    if obj and _clear_single_object_animation(obj):
                        obj_count += 1

                elif item.item_type == 'COLLECTION':
                    col = bpy.data.collections.get(item.collection_name)
                    if col and _clear_collection_animation(col):
                        col_count += 1

            self.report({'INFO'},
                        f"ลบ animation ของ {obj_count} objects + {col_count} collections แล้ว")

        return {'FINISHED'}

# =====================================================================
#  Assign Operators
# =====================================================================

class TURNTABLE_OT_assign_target(Operator):
    """ดึง Object ที่เลือกอยู่ (Active) มาใส่ในช่อง Target"""
    bl_idname = "turntable.assign_target"
    bl_label = "Assign Selected to Target"

    def execute(self, context):
        active = context.view_layer.objects.active
        if active:
            context.scene.turntable_props.target_object = active
            self.report({'INFO'}, f"ตั้งค่า Target เป็น: {active.name}")
        else:
            self.report({'WARNING'}, "กรุณาเลือก Object ในหน้าจอก่อน!")
        return {'FINISHED'}


class TURNTABLE_OT_assign_camera(Operator):
    """ดึง Camera ที่เลือกอยู่ (Active) มาใส่ในช่อง Camera"""
    bl_idname = "turntable.assign_camera"
    bl_label = "Assign Selected to Camera"

    def execute(self, context):
        active = context.view_layer.objects.active
        if active and active.type == 'CAMERA':
            context.scene.turntable_props.camera_object = active
            self.report({'INFO'}, f"ตั้งค่า Camera เป็น: {active.name}")
        elif active:
            self.report({'WARNING'}, "Object ที่เลือกไม่ใช่ Camera!")
        else:
            self.report({'WARNING'}, "กรุณาเลือก Camera ในหน้าจอก่อน!")
        return {'FINISHED'}


# =====================================================================
#  UI Panel
# =====================================================================


def draw_ui(layout, context):
    props = context.scene.turntable_props

    # ── Mode ──
    row = layout.row(align=True)
    row.prop(props, "mode", text="Mode")
    layout.separator()

    # ── Preset ──
    box = layout.box()
    box.label(text="Preset", icon='PRESET')
    box.prop(props, "preset", text="")
    box.separator(factor=0.5)

    # แสดง FPS / Frames (แก้ไขได้)
    row = box.row(align=True)
    row.prop(props, "custom_fps", text="FPS")
    row.prop(props, "custom_frames", text="Frames")

    box.separator(factor=0.5)
    box.prop(props, "model_rotation_style", text="Rotation Style")

    if props.model_rotation_style == 'BACK_FORTH':
        range_row = box.row(align=True)
        range_row.prop(props, "model_back_forth_from", text="From")
        range_row.prop(props, "model_back_forth_to", text="To")
        box.prop(props, "model_back_forth_ease", text="Ease In/Out")

    layout.separator()

    # ── Camera Rotate Mode ──
    if props.mode == 'CAMERA_ROTATE':
        box = layout.box()
        box.label(text="Camera Rotate Settings", icon='OUTLINER_OB_CAMERA')

        # Target
        row = box.row(align=True)
        row.prop(props, "target_object", text="Target", icon='OBJECT_DATA')
        row.operator("turntable.assign_target", text="", icon='RESTRICT_SELECT_OFF')
        
        # Camera
        row = box.row(align=True)
        icon = 'TRIA_DOWN' if props.show_camera_settings else 'TRIA_RIGHT'
        row.prop(props, "show_camera_settings", text="", icon=icon, emboss=False)
        row.prop(props, "camera_object", text="Camera", icon='CAMERA_DATA')
        row.operator("turntable.assign_camera", text="", icon='RESTRICT_SELECT_OFF')

        box.separator(factor=0.5)

        if props.show_camera_settings:
            col = box.column(align=True)
            col.prop(props, "cam_distance", text="Distance")
            col.prop(props, "cam_height", text="Height")
            col.prop(props, "cam_tilt_x", text="Tilt (X)")

            # Step 1: Create Camera
            row = box.row(align=True)
            row.scale_y = 1.4
            row.operator("turntable.create_camera",
                         text="Create / Update Camera",
                         icon='OUTLINER_OB_CAMERA')

        layout.separator()

        # ── Actions ──
        action_box = layout.box()
        action_box.label(text="Actions", icon='PLAY')

        row = action_box.row(align=True)
        row.scale_y = 1.4
        can_start = (props.target_object is not None and
                     props.camera_object is not None)
        row.enabled = can_start
        start_text = (
            "Create Back-Forth Loop"
            if props.model_rotation_style == 'BACK_FORTH'
            else "Start Turntable"
        )
        row.operator("turntable.start_turntable",
                     text=start_text,
                     icon='PLAY')

        action_box.separator(factor=0.5)
        row = action_box.row()
        op = row.operator("turntable.clear_animation",
                          text="Clear Animation",
                          icon='TRASH')
        op.clear_mode = 'CAMERA'

    # ── Model Rotate Mode ──
    else:
        box = layout.box()
        box.label(text="Model Rotate Settings", icon='OBJECT_DATA')

        # ── Add Mode ──
        row = box.row(align=True)
        row.prop(props, "add_mode", expand=True)

        box.separator(factor=0.5)

        # ── UIList ──
        row = box.row()
        row.template_list(
            "TURNTABLE_UL_rotation_items", "",
            props, "rotation_items",
            props, "rotation_items_index",
            rows=4,
        )

        # ── ปุ่มข้างลิสต์ ──
        col = row.column(align=True)
        col.operator("turntable.add_selected", icon='ADD', text="")
        col.operator("turntable.remove_rotation_item", icon='REMOVE', text="")
        col.separator()
        op_up = col.operator("turntable.move_rotation_item", icon='TRIA_UP', text="")
        op_up.direction = 'UP'
        op_down = col.operator("turntable.move_rotation_item", icon='TRIA_DOWN', text="")
        op_down.direction = 'DOWN'

        box.separator(factor=0.5)

        # ── Info ──
        item_count = len(props.rotation_items)
        obj_count = sum(1 for i in props.rotation_items if i.item_type == 'OBJECT')
        col_count = sum(1 for i in props.rotation_items if i.item_type == 'COLLECTION')
        box.label(text=f"Items: {item_count}  (Objects: {obj_count} / Collections: {col_count})",
                  icon='INFO')

        layout.separator()

        # -- Grid Layout --
        grid_box = layout.box()
        grid_box.label(text="Grid Layout", icon='MESH_GRID')

        if item_count > 0:
            cols = min(props.model_grid_columns, item_count)
            rows = math.ceil(item_count / cols) if cols > 0 else 0
            preview = grid_box.row()
            preview.alignment = 'CENTER'
            preview.label(text=f"Result: {cols} col x {rows} row",
                          icon='INFO')

        grid_col = grid_box.column(align=True)
        grid_col.prop(props, "model_grid_columns", text="Columns")
        grid_col.prop(props, "model_grid_spacing", text="Spacing")

        axis_type_row = grid_box.row(align=True)
        axis_type_row.prop(props, "model_grid_primary_axis", text="Axis")
        axis_type_row.prop(props, "model_grid_layout_type", text="Type")

        grid_box.separator(factor=0.5)

        label_header = grid_box.row(align=True)
        icon = 'TRIA_DOWN' if props.model_label_settings_expanded else 'TRIA_RIGHT'
        label_header.prop(
            props,
            "model_label_settings_expanded",
            text="Create Label",
            icon=icon,
            emboss=False,
        )

        if props.model_label_settings_expanded:
            label_row = grid_box.row(align=True)
            label_row.prop(
                props,
                "model_label_show_number",
                text="Create Number",
            )
            label_row.prop(
                props,
                "model_label_show_name",
                text="Create Name",
            )

            labels_enabled = (
                props.model_label_show_name or
                props.model_label_show_number
            )
            label_settings = grid_box.column(align=True)
            label_settings.enabled = labels_enabled
            label_settings.prop(props, "model_label_font_size", text="Font Size")
            label_settings.prop(props, "model_label_gap", text="Gap")

            pos_row = label_settings.row(align=True)
            pos_row.label(text="Position:")
            pos_row.prop(props, "model_label_position", expand=True)

            if labels_enabled:
                parts = []
                if props.model_label_show_number:
                    parts.append("#1")
                if props.model_label_show_name:
                    parts.append("ModelName")
                example = " - ".join(parts)
                hint = grid_box.row()
                hint.alignment = 'CENTER'
                hint.label(text=f'e.g. "{example}"', icon='INFO')

        grid_box.separator(factor=0.5)

        apply_row = grid_box.row(align=True)
        apply_row.scale_y = 1.25
        apply_row.enabled = item_count > 0
        apply_row.operator("turntable.apply_model_grid",
                           text="Apply Grid Layout",
                           icon='MESH_GRID')

        layout.separator()

        # ── Rotate All ──
        action_box = layout.box()
        action_box.label(text="Actions", icon='PLAY')

        row = action_box.row(align=True)
        row.scale_y = 1.4
        row.enabled = item_count > 0
        rotate_text = (
            "Create Back-Forth Loop"
            if props.model_rotation_style == 'BACK_FORTH'
            else "Rotate All"
        )
        row.operator("turntable.rotate_models",
                     text=rotate_text,
                     icon='PLAY')

        # ── Clear ──
        action_box.separator(factor=0.5)
        row = action_box.row()
        row.enabled = item_count > 0
        op = row.operator("turntable.clear_animation",
                          text="Clear All Animation",
                          icon='TRASH')
        op.clear_mode = 'MODELS'


# =====================================================================
#  Registration
# =====================================================================

classes = (
    TURNTABLE_RotationItem,
    TURNTABLE_Properties,
    TURNTABLE_UL_rotation_items,
    TURNTABLE_OT_add_selected,
    TURNTABLE_OT_remove_rotation_item,
    TURNTABLE_OT_move_rotation_item,
    TURNTABLE_OT_apply_model_grid,
    TURNTABLE_OT_create_camera,
    TURNTABLE_OT_start_turntable,
    TURNTABLE_OT_rotate_models,
    TURNTABLE_OT_clear_animation,
    TURNTABLE_OT_assign_target,
    TURNTABLE_OT_assign_camera,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.turntable_props = PointerProperty(type=TURNTABLE_Properties)


def unregister():
    del bpy.types.Scene.turntable_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
