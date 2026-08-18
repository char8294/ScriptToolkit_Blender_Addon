"""Script Toolkit's native FBX import/export options.

This module combines the Universal FBX root-bone workflow with the FBX
Armature-node export patch.  The native Blender FBX operator remains the
entry point; this module only extends its option panels and post-processes an
import when the Universal Root Bone option is enabled.
"""

import importlib
import struct
import sys

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Panel


_CORE = None
_EXPORT = None
_UTILS = None
_ORIG = {}
_OWNED_SCENE_PROPERTIES = set()
_EXPORT_ACTIVE = False
_IMPORT_PROCESSING = False
_PATCH_ERROR = ""
_KNOWN_OBJECT_NAMES = None
_PENDING_IMPORT_NAMES = set()
_PENDING_IMPORT_RETRIES = {}
_PROCESSED_IMPORT_NAMES = set()


_ROOT_MODE_ITEMS = (
    (
        "AUTO",
        "Auto Detect",
        "Use an existing standard root or create one automatically",
    ),
    (
        "CUSTOM",
        "Custom Name",
        "Create the master root bone with a custom name",
    ),
    (
        "EXISTING",
        "Use Existing Top Bone",
        "Keep the first top-level bone as the root",
    ),
)


def _find_core_fbx_module():
    """Return Blender's bundled io_scene_fbx package."""
    try:
        return importlib.import_module("io_scene_fbx")
    except ImportError:
        pass

    # Some Blender installations expose bundled extensions through a
    # namespace package instead of the top-level module name.
    for mod_name, mod in tuple(sys.modules.items()):
        if mod_name.endswith(".io_scene_fbx") and hasattr(mod, "ExportFBX"):
            return mod

    raise ImportError(
        "Blender's built-in FBX Import-Export module could not be found. "
        "Enable the FBX format add-on/extension first."
    )


def _is_armature_wrapper(obj):
    try:
        return obj.is_object and obj.type == "ARMATURE"
    except Exception:
        return False


def _register_scene_property(name, definition):
    if hasattr(bpy.types.Scene, name):
        return
    setattr(bpy.types.Scene, name, definition)
    _OWNED_SCENE_PROPERTIES.add(name)


def _register_scene_properties():
    # Keep the export property's original name for compatibility with the
    # standalone FBX Ignore Armature Node add-on.
    _register_scene_property(
        "fbx_ignore_armature_node",
        BoolProperty(
            name="Ignore Armature Node",
            description=(
                "Do not export the Blender Armature object as an FBX Model/Null. "
                "Promote root bones/direct children to the FBX scene root."
            ),
            default=False,
        ),
    )
    _register_scene_property(
        "fbx_universal_root_enabled",
        BoolProperty(
            name="Universal Root Bone",
            description=(
                "Create or use a master root bone after the native FBX import "
                "and transfer object animation to it"
            ),
            default=False,
        ),
    )
    _register_scene_property(
        "fbx_universal_root_mode",
        EnumProperty(
            name="Root Bone Mode",
            description="Choose how the imported root bone should be handled",
            items=_ROOT_MODE_ITEMS,
            default="AUTO",
        ),
    )
    _register_scene_property(
        "fbx_universal_custom_root_name",
        StringProperty(
            name="Custom Root Name",
            description="Name of the root bone to create in Custom Name mode",
            default="root",
        ),
    )
    _register_scene_property(
        "fbx_universal_transfer_anim",
        BoolProperty(
            name="Transfer Object Anim to Root",
            description=(
                "Transfer object location, rotation and scale keyframes to "
                "the master root pose bone"
            ),
            default=True,
        ),
    )
    _register_scene_property(
        "fbx_universal_parent_all_roots",
        BoolProperty(
            name="Parent All Root Bones",
            description="Parent all imported top-level bones under the master root",
            default=True,
        ),
    )
    _register_scene_property(
        "fbx_universal_auto_frame_range",
        BoolProperty(
            name="Auto Frame Range",
            description="Set the scene frame range to the imported animation keys",
            default=True,
        ),
    )


def _unregister_scene_properties():
    for name in tuple(_OWNED_SCENE_PROPERTIES):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    _OWNED_SCENE_PROPERTIES.clear()


def get_action_fcurves(action):
    """Read action curves across Blender's legacy and slotted Actions."""
    if not action:
        return []

    try:
        fcurves = list(action.fcurves)
    except (AttributeError, RuntimeError, TypeError):
        fcurves = []
    if fcurves:
        return fcurves

    # Blender 4/5 slotted Actions expose curves through layers/strips.
    try:
        layers = action.layers
    except (AttributeError, RuntimeError, TypeError):
        layers = ()
    for layer in layers:
        for strip in getattr(layer, "strips", ()):
            for channelbag in getattr(strip, "channelbags", ()):
                try:
                    fcurves.extend(list(channelbag.fcurves))
                except (AttributeError, RuntimeError, TypeError):
                    continue
    if fcurves:
        return fcurves

    try:
        return list(action.curves)
    except (AttributeError, RuntimeError, TypeError):
        return fcurves


def _standard_root_name(arm, top_bones):
    """Choose the same useful root names as the original Universal importer."""
    arm_clean_name = arm.name.split(".")[0]
    top_names = [bone.name.lower() for bone in top_bones]
    standard_names = {"root", "armature_root", "master", "main"}

    for bone in top_bones:
        if bone.name.lower() in standard_names:
            return bone.name
    if "bip" in arm_clean_name.lower():
        return arm_clean_name
    if any("bip" in name for name in top_names):
        return "Bip001"
    return "root"


def process_universal_armature(
    arm,
    root_mode="AUTO",
    custom_root_name="root",
    transfer_anim=True,
    auto_frame_range=True,
    parent_all_roots=True,
):
    """Create/configure a root bone and move object animation to it."""
    if not arm or arm.type != "ARMATURE":
        return False, "Selected object is not an Armature."

    bpy.context.view_layer.objects.active = arm
    if arm.mode != "EDIT":
        bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = arm.data.edit_bones
    top_bones = [bone for bone in edit_bones if bone.parent is None]
    if not top_bones:
        bpy.ops.object.mode_set(mode="OBJECT")
        return False, "No bones found in Armature."

    if root_mode == "CUSTOM":
        target_root_name = custom_root_name or "root"
    elif root_mode == "EXISTING":
        target_root_name = top_bones[0].name
    else:
        target_root_name = _standard_root_name(arm, top_bones)

    root_eb = edit_bones.get(target_root_name)
    # A custom name may collide with a non-root bone.  Never turn that child
    # into the new root; let Blender generate a unique name instead.
    if root_eb is not None and root_eb.parent is not None:
        root_eb = None
    if root_eb is None:
        root_eb = edit_bones.new(target_root_name)
        root_eb.head = (0.0, 0.0, 0.0)
        root_eb.tail = (0.0, 1.0, 0.0)
        root_eb.roll = 0.0
        target_root_name = root_eb.name

    if parent_all_roots:
        for top_bone in top_bones:
            if top_bone != root_eb and top_bone.parent is None:
                top_bone.parent = root_eb
    elif top_bones and top_bones[0] != root_eb:
        top_bones[0].parent = root_eb

    bpy.ops.object.mode_set(mode="OBJECT")

    if transfer_anim and arm.animation_data and arm.animation_data.action:
        action = arm.animation_data.action
        pose_root = arm.pose.bones.get(target_root_name)
        if pose_root:
            valid_rotation_modes = {
                "QUATERNION",
                "AXIS_ANGLE",
                "XYZ",
                "XZY",
                "YXZ",
                "YZX",
                "ZXY",
                "ZYX",
            }
            pose_root.rotation_mode = (
                arm.rotation_mode
                if arm.rotation_mode in valid_rotation_modes
                else "XYZ"
            )

        scale_factor = 1.0 / arm.scale.x if arm.scale.x != 0 else 1.0
        fcurves = get_action_fcurves(action)
        for fcurve in fcurves:
            if fcurve.data_path == "location":
                fcurve.data_path = f'pose.bones["{target_root_name}"].location'
                for keyframe in fcurve.keyframe_points:
                    keyframe.co[1] *= scale_factor
            elif fcurve.data_path in {
                "rotation_euler",
                "rotation_quaternion",
                "rotation_axis_angle",
            }:
                fcurve.data_path = (
                    f'pose.bones["{target_root_name}"].{fcurve.data_path}'
                )
            elif fcurve.data_path == "scale":
                fcurve.data_path = f'pose.bones["{target_root_name}"].scale'
                for keyframe in fcurve.keyframe_points:
                    keyframe.co[1] *= scale_factor

        if auto_frame_range and fcurves:
            key_times = [
                keyframe.co[0]
                for fcurve in fcurves
                for keyframe in fcurve.keyframe_points
            ]
            if key_times:
                bpy.context.scene.frame_start = int(min(key_times))
                bpy.context.scene.frame_end = int(max(key_times))

    # The original workflow normalizes the Armature object after moving its
    # object-level transforms to the new root bone.
    arm.location = (0.0, 0.0, 0.0)
    if arm.rotation_mode == "QUATERNION":
        arm.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    elif arm.rotation_mode == "AXIS_ANGLE":
        arm.rotation_axis_angle = (0.0, 0.0, 0.0, 0.0)
    else:
        arm.rotation_euler = (0.0, 0.0, 0.0)

    return True, f"Successfully processed Armature with Root Bone '{target_root_name}'."


def _process_armature_in_context(arm, context):
    """Run the edit-mode operation while preserving the user's selection."""
    previous_active = context.view_layer.objects.active
    previous_selection = {
        obj.name: obj.select_get()
        for obj in context.view_layer.objects
        if obj.select_get()
    }
    try:
        for obj in context.selected_objects:
            obj.select_set(False)
        arm.select_set(True)
        context.view_layer.objects.active = arm
        scene = context.scene
        return process_universal_armature(
            arm=arm,
            root_mode=scene.fbx_universal_root_mode,
            custom_root_name=scene.fbx_universal_custom_root_name,
            transfer_anim=scene.fbx_universal_transfer_anim,
            auto_frame_range=scene.fbx_universal_auto_frame_range,
            parent_all_roots=scene.fbx_universal_parent_all_roots,
        )
    finally:
        for obj in context.view_layer.objects:
            obj.select_set(previous_selection.get(obj.name, False))
        if previous_active and previous_active.name in bpy.data.objects:
            context.view_layer.objects.active = previous_active


def _new_armatures(before_names):
    return [
        obj
        for obj in bpy.data.objects
        if obj.name not in before_names and obj.type == "ARMATURE"
    ]


def _operator_identifier(context):
    """Return the active File Browser operator's RNA identifier."""
    space = getattr(context, "space_data", None)
    operator = getattr(space, "active_operator", None)
    if operator is None:
        return ""
    # Blender 5.2's C-side FBX operators expose the useful identifier through
    # bl_idname (for example ``WM_OT_fbx_import``), while bl_rna.identifier is
    # only the generic ``Operator``.
    operator_id = getattr(operator, "bl_idname", "")
    if operator_id:
        return operator_id
    rna = getattr(operator, "bl_rna", None)
    identifier = getattr(rna, "identifier", "")
    if identifier:
        return identifier
    return getattr(operator, "bl_idname", "")


def _schedule_pending_import_processing():
    try:
        if not bpy.app.timers.is_registered(_process_pending_imports_timer):
            bpy.app.timers.register(
                _process_pending_imports_timer,
                first_interval=0.2,
            )
    except (AttributeError, RuntimeError, ValueError):
        pass


def _process_pending_imports_timer():
    """Process armatures created by Blender's new C-side FBX importer."""
    if not hasattr(bpy.types.Scene, "fbx_universal_root_enabled"):
        _PENDING_IMPORT_NAMES.clear()
        _PENDING_IMPORT_RETRIES.clear()
        return None

    scene = bpy.context.scene
    if not getattr(scene, "fbx_universal_root_enabled", False):
        _PENDING_IMPORT_NAMES.clear()
        _PENDING_IMPORT_RETRIES.clear()
        return None

    retry = False
    for name in tuple(_PENDING_IMPORT_NAMES):
        if name in _PROCESSED_IMPORT_NAMES:
            _PENDING_IMPORT_NAMES.discard(name)
            _PENDING_IMPORT_RETRIES.pop(name, None)
            continue

        arm = bpy.data.objects.get(name)
        if arm is None or arm.type != "ARMATURE":
            _PENDING_IMPORT_NAMES.discard(name)
            _PENDING_IMPORT_RETRIES.pop(name, None)
            continue

        if not arm.data.bones:
            attempts = _PENDING_IMPORT_RETRIES.get(name, 0) + 1
            _PENDING_IMPORT_RETRIES[name] = attempts
            if attempts < 25:
                retry = True
                continue
            _PENDING_IMPORT_NAMES.discard(name)
            _PENDING_IMPORT_RETRIES.pop(name, None)
            continue

        try:
            success, message = _process_armature_in_context(arm, bpy.context)
        except Exception as error:
            print(f"[Script Toolkit / FBX] Universal import failed: {error}")
            success = False
            message = str(error)
        if success:
            print(f"[Script Toolkit / FBX] {message}")
        else:
            print(f"[Script Toolkit / FBX] {message}")
        _PROCESSED_IMPORT_NAMES.add(name)
        _PENDING_IMPORT_NAMES.discard(name)
        _PENDING_IMPORT_RETRIES.pop(name, None)

    if retry or _PENDING_IMPORT_NAMES:
        return 0.2
    return None


def _depsgraph_update_post(scene, _depsgraph):
    """Queue newly created armatures from the native wm.fbx_import operator."""
    global _KNOWN_OBJECT_NAMES

    try:
        current_names = set(bpy.data.objects.keys())
    except (AttributeError, RuntimeError):
        return
    if _KNOWN_OBJECT_NAMES is None:
        _KNOWN_OBJECT_NAMES = current_names
        return
    new_names = current_names - _KNOWN_OBJECT_NAMES
    _KNOWN_OBJECT_NAMES = current_names

    if not getattr(scene, "fbx_universal_root_enabled", False):
        return

    new_armature_names = {
        name
        for name in new_names
        if bpy.data.objects.get(name) is not None
        and bpy.data.objects[name].type == "ARMATURE"
    }
    if new_armature_names:
        _PENDING_IMPORT_NAMES.update(new_armature_names)
        _schedule_pending_import_processing()


def _initialize_import_tracking_timer():
    """Capture the object baseline after Blender leaves restricted registration context."""
    global _KNOWN_OBJECT_NAMES

    try:
        _KNOWN_OBJECT_NAMES = set(bpy.data.objects.keys())
    except (AttributeError, RuntimeError):
        return 0.2
    return None


def _schedule_import_tracking_initialization():
    try:
        if not bpy.app.timers.is_registered(_initialize_import_tracking_timer):
            bpy.app.timers.register(_initialize_import_tracking_timer, first_interval=0.2)
    except (AttributeError, RuntimeError, ValueError):
        pass


def _start_import_tracking():
    global _KNOWN_OBJECT_NAMES
    # Blender calls add-on register() with restricted bpy.data during install.
    # Initialize the baseline from a timer once normal data access is available.
    _KNOWN_OBJECT_NAMES = None
    _PENDING_IMPORT_NAMES.clear()
    _PENDING_IMPORT_RETRIES.clear()
    _PROCESSED_IMPORT_NAMES.clear()
    if _depsgraph_update_post not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph_update_post)
    _schedule_import_tracking_initialization()


def _stop_import_tracking():
    try:
        while _depsgraph_update_post in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_update_post)
    except (AttributeError, RuntimeError, ValueError):
        pass
    try:
        if bpy.app.timers.is_registered(_process_pending_imports_timer):
            bpy.app.timers.unregister(_process_pending_imports_timer)
    except (AttributeError, RuntimeError, ValueError):
        pass
    try:
        if bpy.app.timers.is_registered(_initialize_import_tracking_timer):
            bpy.app.timers.unregister(_initialize_import_tracking_timer)
    except (AttributeError, RuntimeError, ValueError):
        pass
    global _KNOWN_OBJECT_NAMES
    _KNOWN_OBJECT_NAMES = None
    _PENDING_IMPORT_NAMES.clear()
    _PENDING_IMPORT_RETRIES.clear()
    _PROCESSED_IMPORT_NAMES.clear()


def _patched_import_execute(self, context):
    global _IMPORT_PROCESSING

    if _IMPORT_PROCESSING:
        return _ORIG["import_execute"](self, context)

    before_names = set(bpy.data.objects.keys())
    result = _ORIG["import_execute"](self, context)
    scene = getattr(context, "scene", None)
    if result != {"FINISHED"} or scene is None:
        return result
    if not getattr(scene, "fbx_universal_root_enabled", False):
        return result

    _IMPORT_PROCESSING = True
    try:
        for arm in _new_armatures(before_names):
            try:
                success, message = _process_armature_in_context(arm, context)
            except Exception as error:
                self.report({"WARNING"}, f"Universal root processing failed: {error}")
                continue
            if success:
                self.report({"INFO"}, message)
            else:
                self.report({"WARNING"}, message)
            _PROCESSED_IMPORT_NAMES.add(arm.name)
    finally:
        _IMPORT_PROCESSING = False
    return result


class FILEBROWSER_PT_script_toolkit_fbx(Panel):
    """Separate Script Toolkit panel for Blender's current FBX operators."""

    bl_idname = "FILEBROWSER_PT_script_toolkit_fbx"
    bl_label = "Script Toolkit"
    bl_space_type = "FILE_BROWSER"
    bl_region_type = "TOOL_PROPS"
    bl_parent_id = "FILE_PT_operator"

    @classmethod
    def poll(cls, context):
        return _operator_identifier(context) in {
            "WM_OT_fbx_import",
            "wm.fbx_import",
            "WM_OT_fbx_export",
            "wm.fbx_export",
            "EXPORT_SCENE_OT_fbx",
            "export_scene.fbx",
        }

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        scene = context.scene
        operator_id = _operator_identifier(context)

        if operator_id in {"WM_OT_fbx_import", "wm.fbx_import"}:
            layout.label(text="Universal Root Bone", icon="BONE_DATA")
            layout.prop(scene, "fbx_universal_root_enabled")
            if scene.fbx_universal_root_enabled:
                layout.prop(scene, "fbx_universal_root_mode")
                if scene.fbx_universal_root_mode == "CUSTOM":
                    layout.prop(scene, "fbx_universal_custom_root_name")
                layout.prop(scene, "fbx_universal_parent_all_roots")
                layout.prop(scene, "fbx_universal_transfer_anim")
                layout.prop(scene, "fbx_universal_auto_frame_range")
        else:
            layout.label(text="FBX Export Options", icon="EXPORT")
            layout.prop(
                scene,
                "fbx_ignore_armature_node",
                text="Ignore Armature Node",
            )
            layout.label(
                text="Promote root bones to the FBX scene root",
                icon="INFO",
            )


# -----------------------------------------------------------------------------
# Native FBX export patch from FBX Ignore Armature Node v0.2
# -----------------------------------------------------------------------------


def _patched_export_panel_armature(layout, operator):
    """Blender 5.2 Armature export panel plus Ignore Armature Node."""
    header, body = layout.panel("FBX_export_armature", default_closed=True)
    header.label(text="Armature")
    if body:
        body.prop(operator, "primary_bone_axis")
        body.prop(operator, "secondary_bone_axis")
        body.prop(operator, "armature_nodetype")
        body.prop(operator, "use_armature_deform_only")
        body.prop(operator, "add_leaf_bones")


def _patched_fbx_object_matrix(
    self,
    scene_data,
    rest=False,
    local_space=False,
    global_space=False,
):
    """Preserve promoted root-bone transforms when the Armature node is omitted."""
    if _EXPORT_ACTIVE and not local_space and not global_space:
        try:
            if self.is_bone:
                parent = self.parent
                if parent is not None and _is_armature_wrapper(parent):
                    return _ORIG["fbx_object_matrix"](
                        self,
                        scene_data,
                        rest=rest,
                        local_space=False,
                        global_space=True,
                    )
        except Exception:
            pass

    return _ORIG["fbx_object_matrix"](
        self,
        scene_data,
        rest=rest,
        local_space=local_space,
        global_space=global_space,
    )


def _patched_fbx_animations_do(
    scene_data,
    ref_id,
    f_start,
    f_end,
    start_zero,
    objects=None,
    force_keep=False,
):
    if not _EXPORT_ACTIVE:
        return _ORIG["fbx_animations_do"](
            scene_data,
            ref_id,
            f_start,
            f_end,
            start_zero,
            objects=objects,
            force_keep=force_keep,
        )

    sample_objects = set(scene_data.objects if objects is None else objects)
    for obj in tuple(sample_objects):
        if not _is_armature_wrapper(obj):
            continue
        try:
            sample_objects.update(
                bone for bone in obj.bones if bone in scene_data.objects
            )
        except Exception:
            pass
        sample_objects.discard(obj)

    return _ORIG["fbx_animations_do"](
        scene_data,
        ref_id,
        f_start,
        f_end,
        start_zero,
        objects=sample_objects,
        force_keep=force_keep,
    )


def _patched_fbx_data_empty_elements(root, empty, scene_data):
    if _EXPORT_ACTIVE and _is_armature_wrapper(empty):
        return None
    return _ORIG["fbx_data_empty_elements"](root, empty, scene_data)


def _patched_fbx_data_object_elements(root, ob_obj, scene_data):
    if _EXPORT_ACTIVE and _is_armature_wrapper(ob_obj):
        return None
    return _ORIG["fbx_data_object_elements"](root, ob_obj, scene_data)


def _elem_first(parent, elem_id):
    for elem in getattr(parent, "elems", ()):
        if elem.id == elem_id:
            return elem
    return None


def _read_packed_i64(elem):
    if elem is None or not elem.props:
        return None
    try:
        return struct.unpack("<q", elem.props[0])[0]
    except Exception:
        return None


def _patched_fbx_data_bindpose_element(
    root,
    me_obj,
    me,
    scene_data,
    arm_obj=None,
    mat_world_arm=None,
    bones=[],
):
    before = len(root.elems)
    result = _ORIG["fbx_data_bindpose_element"](
        root,
        me_obj,
        me,
        scene_data,
        arm_obj=arm_obj,
        mat_world_arm=mat_world_arm,
        bones=bones,
    )

    if not _EXPORT_ACTIVE or arm_obj is None or arm_obj == me_obj:
        return result

    new_elems = root.elems[before:]
    pose = next((elem for elem in new_elems if elem.id == b"Pose"), None)
    if pose is None:
        return result

    arm_uuid = int(arm_obj.fbx_uuid)
    kept = []
    removed = 0
    for elem in pose.elems:
        if elem.id != b"PoseNode":
            kept.append(elem)
            continue
        node_ref = _elem_first(elem, b"Node")
        if _read_packed_i64(node_ref) == arm_uuid:
            removed += 1
            continue
        kept.append(elem)

    if removed:
        pose.elems[:] = kept
        pose_node_count = sum(1 for elem in pose.elems if elem.id == b"PoseNode")
        nb_pose_nodes = _elem_first(pose, b"NbPoseNodes")
        if nb_pose_nodes is not None and nb_pose_nodes.props:
            nb_pose_nodes.props[0] = struct.pack("<i", pose_node_count)

    return result


def _decrement_template(templates, key, amount):
    if amount <= 0 or key not in templates:
        return
    template = templates[key]
    new_count = max(0, template.nbr_users - amount)
    if new_count == 0:
        del templates[key]
    else:
        templates[key] = template._replace(nbr_users=new_count)


def _patched_fbx_data_from_scene(scene, depsgraph, settings):
    scene_data = _ORIG["fbx_data_from_scene"](scene, depsgraph, settings)
    if not _EXPORT_ACTIVE:
        return scene_data

    armatures = tuple(
        obj for obj in scene_data.objects if _is_armature_wrapper(obj)
    )
    if not armatures:
        return scene_data

    arm_model_ids = {int(arm.fbx_uuid) for arm in armatures}
    arm_attr_ids = set()
    for arm in armatures:
        empty_key = scene_data.data_empties.get(arm)
        if empty_key is not None:
            arm_attr_ids.add(int(_UTILS.get_fbx_uuid_from_key(empty_key)))

    templates = dict(scene_data.templates)
    _decrement_template(templates, b"Model", len(armatures))
    _decrement_template(templates, b"Null", len(arm_attr_ids))
    templates_users = sum(template.nbr_users for template in templates.values())

    connections = []
    seen = set()
    for conn in scene_data.connections:
        c_type, src, dst, prop = conn
        src_i = int(src)
        dst_i = int(dst)

        if src_i in arm_attr_ids or dst_i in arm_attr_ids:
            continue
        if src_i in arm_model_ids:
            continue
        if dst_i in arm_model_ids:
            if c_type == b"OO":
                conn = (b"OO", src, 0, prop)
            else:
                continue

        key = (conn[0], int(conn[1]), int(conn[2]), conn[3])
        if key in seen:
            continue
        seen.add(key)
        connections.append(conn)

    return scene_data._replace(
        templates=templates,
        templates_users=templates_users,
        connections=connections,
    )


def _patched_save(operator, context, *args, **kwargs):
    global _EXPORT_ACTIVE
    previous = _EXPORT_ACTIVE
    try:
        _EXPORT_ACTIVE = bool(
            getattr(context.scene, "fbx_ignore_armature_node", False)
        )
        return _ORIG["save"](operator, context, *args, **kwargs)
    finally:
        _EXPORT_ACTIVE = previous


def _patch_is_active():
    """Check whether Blender has reloaded io_scene_fbx since our last patch."""
    return bool(
        _ORIG
        and _CORE is not None
        and _EXPORT is not None
        and _UTILS is not None
        and getattr(_CORE, "export_panel_armature", None)
        is _patched_export_panel_armature
        and getattr(getattr(_CORE, "ImportFBX", None), "execute", None)
        is _patched_import_execute
        and getattr(_EXPORT, "save", None) is _patched_save
        and getattr(_EXPORT, "fbx_animations_do", None)
        is _patched_fbx_animations_do
        and getattr(_EXPORT, "fbx_data_empty_elements", None)
        is _patched_fbx_data_empty_elements
        and getattr(_EXPORT, "fbx_data_object_elements", None)
        is _patched_fbx_data_object_elements
        and getattr(_EXPORT, "fbx_data_bindpose_element", None)
        is _patched_fbx_data_bindpose_element
        and getattr(_EXPORT, "fbx_data_from_scene", None)
        is _patched_fbx_data_from_scene
        and getattr(_UTILS.ObjectWrapper, "fbx_object_matrix", None)
        is _patched_fbx_object_matrix
    )


def _discard_stale_patch_state():
    """Forget originals from a module object that Blender has reloaded."""
    global _CORE, _EXPORT, _UTILS
    _ORIG.clear()
    _CORE = None
    _EXPORT = None
    _UTILS = None


def _patch_retry_timer():
    """Re-apply patches if Blender reloads its bundled FBX module later."""
    if not hasattr(bpy.types.Scene, "fbx_universal_root_enabled"):
        return None
    if not _patch_is_active():
        _install_patches()
    # Blender can reload built-in extensions after startup and after an
    # extension update.  Keep this inexpensive check alive while Script
    # Toolkit is registered; unregister() removes the timer.
    return 1.0


def _schedule_patch_retry():
    try:
        if not bpy.app.timers.is_registered(_patch_retry_timer):
            bpy.app.timers.register(_patch_retry_timer, first_interval=0.5)
    except (AttributeError, RuntimeError, ValueError):
        pass


def _unschedule_patch_retry():
    try:
        if bpy.app.timers.is_registered(_patch_retry_timer):
            bpy.app.timers.unregister(_patch_retry_timer)
    except (AttributeError, RuntimeError, ValueError):
        pass


def _install_patches():
    global _CORE, _EXPORT, _UTILS, _PATCH_ERROR

    if _patch_is_active():
        return True
    if _ORIG:
        # The bundled io_scene_fbx module was reloaded after our previous
        # installation.  Its functions are native again, so capture those
        # current functions as the new originals.
        _discard_stale_patch_state()

    try:
        _CORE = _find_core_fbx_module()
        _EXPORT = importlib.import_module(_CORE.__name__ + ".export_fbx_bin")
        _UTILS = importlib.import_module(_CORE.__name__ + ".fbx_utils")

        _ORIG.update(
            {
                "export_panel_armature": _CORE.export_panel_armature,
                "import_execute": _CORE.ImportFBX.execute,
                "save": _EXPORT.save,
                "fbx_object_matrix": _UTILS.ObjectWrapper.fbx_object_matrix,
                "fbx_animations_do": _EXPORT.fbx_animations_do,
                "fbx_data_empty_elements": _EXPORT.fbx_data_empty_elements,
                "fbx_data_object_elements": _EXPORT.fbx_data_object_elements,
                "fbx_data_bindpose_element": _EXPORT.fbx_data_bindpose_element,
                "fbx_data_from_scene": _EXPORT.fbx_data_from_scene,
            }
        )

        _CORE.export_panel_armature = _patched_export_panel_armature
        _CORE.ImportFBX.execute = _patched_import_execute
        _EXPORT.save = _patched_save
        _UTILS.ObjectWrapper.fbx_object_matrix = _patched_fbx_object_matrix
        _EXPORT.fbx_animations_do = _patched_fbx_animations_do
        _EXPORT.fbx_data_empty_elements = _patched_fbx_data_empty_elements
        _EXPORT.fbx_data_object_elements = _patched_fbx_data_object_elements
        _EXPORT.fbx_data_bindpose_element = _patched_fbx_data_bindpose_element
        _EXPORT.fbx_data_from_scene = _patched_fbx_data_from_scene
    except Exception as error:
        _PATCH_ERROR = str(error)
        _ORIG.clear()
        _CORE = None
        _EXPORT = None
        _UTILS = None
        return False

    _PATCH_ERROR = ""
    return True


def _remove_patches():
    global _EXPORT_ACTIVE, _IMPORT_PROCESSING, _PATCH_ERROR

    _EXPORT_ACTIVE = False
    _IMPORT_PROCESSING = False
    if not _ORIG:
        return
    if not _patch_is_active():
        # Blender already restored/reloaded the bundled module.  Do not put
        # stale function objects from the previous module incarnation back.
        _discard_stale_patch_state()
        _PATCH_ERROR = ""
        return

    if _CORE is not None:
        _CORE.export_panel_armature = _ORIG["export_panel_armature"]
        _CORE.ImportFBX.execute = _ORIG["import_execute"]
    if _EXPORT is not None:
        _EXPORT.save = _ORIG["save"]
        _EXPORT.fbx_animations_do = _ORIG["fbx_animations_do"]
        _EXPORT.fbx_data_empty_elements = _ORIG["fbx_data_empty_elements"]
        _EXPORT.fbx_data_object_elements = _ORIG["fbx_data_object_elements"]
        _EXPORT.fbx_data_bindpose_element = _ORIG["fbx_data_bindpose_element"]
        _EXPORT.fbx_data_from_scene = _ORIG["fbx_data_from_scene"]
    if _UTILS is not None:
        _UTILS.ObjectWrapper.fbx_object_matrix = _ORIG["fbx_object_matrix"]

    _ORIG.clear()
    _PATCH_ERROR = ""


def draw_ui(layout, context):
    """Explain where the real options were added; no duplicate settings here."""
    box = layout.box()
    box.label(text="Native FBX options extended", icon="FILE_3D")
    box.label(text="Import > FBX (.fbx) > Script Toolkit:", icon="IMPORT")
    box.label(text="• Universal Root Bone")
    box.label(text="• Root Mode / Custom Root Name")
    box.label(text="• Parent All Root Bones")
    box.label(text="• Transfer Object Animation to Root")
    box.label(text="• Auto Frame Range")

    box.separator()
    box.label(text="Export > FBX (.fbx) > Script Toolkit:", icon="EXPORT")
    box.label(text="• Ignore Armature Node")
    box.label(text="  Promotes root bones to the FBX scene root")

    box.separator()
    box.label(text="ตัวเลือกจริงอยู่ในหน้าต่าง FBX เดิมของ Blender", icon="INFO")
    if _PATCH_ERROR:
        box.label(text="FBX module ยังไม่พร้อมใช้งาน", icon="ERROR")
        box.label(text="เปิดใช้ FBX Import-Export แล้ว reload add-on")


CLASSES = (FILEBROWSER_PT_script_toolkit_fbx,)


def register():
    _register_scene_properties()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    _start_import_tracking()
    _install_patches()
    _schedule_patch_retry()


def unregister():
    _stop_import_tracking()
    _unschedule_patch_retry()
    _remove_patches()
    for cls in reversed(CLASSES):
        if hasattr(bpy.types, cls.__name__):
            bpy.utils.unregister_class(cls)
    _unregister_scene_properties()
