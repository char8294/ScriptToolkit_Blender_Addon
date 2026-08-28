"""Script Toolkit options and export-time fixes for the Better FBX exporter."""

import importlib
import math
import sys

import bpy
from bpy.props import BoolProperty
from bpy.types import Panel
from mathutils import Matrix, Vector


_BETTER = None
_BETTER_ORIG_EXECUTE = None
_BETTER_EXPORT_ACTIVE = False
_OWNED_SCENE_PROPERTIES = set()

_BETTER_EXPORT_OPERATOR_IDS = frozenset(
    {"BETTER_EXPORT_OT_fbx", "better_export.fbx"}
)


def _register_scene_property(name, definition):
    if hasattr(bpy.types.Scene, name):
        return
    setattr(bpy.types.Scene, name, definition)
    _OWNED_SCENE_PROPERTIES.add(name)


def _register_scene_properties():
    _register_scene_property(
        "better_fbx_bake_mesh_transforms",
        BoolProperty(
            name="Bake Mesh Rotation/Scale for Unity",
            description=(
                "Bake mesh rotation/scale into the mesh data so Unity imports "
                "mesh nodes at rotation 0,0,0 and scale 1,1,1. When needed, "
                "Optimize For Game Engine is disabled and FBX Unit is set to m "
                "temporarily for this export. Bones are unchanged."
            ),
            default=False,
        ),
    )


def _unregister_scene_properties():
    for name in tuple(_OWNED_SCENE_PROPERTIES):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    _OWNED_SCENE_PROPERTIES.clear()


def _find_better_export_module():
    """Return Better FBX's exporter module when the optional add-on is loaded."""
    for mod_name, mod in tuple(sys.modules.items()):
        if mod_name.endswith(".better_fbx.exporter") and hasattr(
            mod, "BetterExportFbx"
        ):
            return mod
    try:
        return importlib.import_module("bl_ext.user_default.better_fbx.exporter")
    except (ImportError, ModuleNotFoundError):
        return None


def _operator_identifier(context):
    space = getattr(context, "space_data", None)
    operator = getattr(space, "active_operator", None)
    if operator is None:
        return ""
    operator_id = getattr(operator, "bl_idname", "")
    if operator_id:
        return operator_id
    rna = getattr(operator, "bl_rna", None)
    return getattr(rna, "identifier", "")


def _better_patch_is_active():
    if _BETTER is None or _BETTER_ORIG_EXECUTE is None:
        return False
    operator_class = getattr(_BETTER, "BetterExportFbx", None)
    return (
        operator_class is not None
        and getattr(operator_class, "execute", None)
        is _patched_better_execute
    )


def _is_skinned_mesh(obj):
    if obj.type != "MESH":
        return False
    if any(modifier.type == "ARMATURE" for modifier in obj.modifiers):
        return True
    return bool(
        obj.parent
        and obj.parent.type == "ARMATURE"
        and len(obj.vertex_groups) > 0
    )


def _matrix_without_translation(matrix):
    result = matrix.copy()
    result.translation = Vector((0.0, 0.0, 0.0))
    return result


def _better_export_mesh_objects(context, operator):
    """Match Better FBX's object filters closely enough for the pre-bake."""
    if getattr(operator, "use_separate_collection", False):
        objects = []
        for layer_collection in context.view_layer.layer_collection.children:
            if not layer_collection.visible_get():
                continue
            objects.extend(layer_collection.collection.all_objects)
    elif getattr(operator, "use_active_collection", False):
        objects = list(
            context.view_layer.active_layer_collection.collection.all_objects
        )
    else:
        objects = list(context.view_layer.objects)

    if getattr(operator, "use_selection", False):
        objects = [obj for obj in objects if obj.select_get()]
    if getattr(operator, "use_visible", False):
        objects = [obj for obj in objects if obj.visible_get()]

    seen = set()
    meshes = []
    for obj in objects:
        if obj.type != "MESH" or obj.name in seen:
            continue
        seen.add(obj.name)
        meshes.append(obj)
    return tuple(meshes)


def _bake_better_mesh_transforms(mesh_objects):
    """Bake mesh transforms and return state needed to restore the scene."""
    saved = []
    skinned_node_rotation = Matrix.Rotation(math.radians(90.0), 4, "X")
    identity = Matrix.Identity(4)

    for obj in mesh_objects:
        original_data = obj.data
        original_basis = obj.matrix_basis.copy()
        original_rotation_mode = obj.rotation_mode
        original_location = tuple(obj.location)
        original_rotation_euler = tuple(obj.rotation_euler)
        original_rotation_quaternion = tuple(obj.rotation_quaternion)
        original_rotation_axis_angle = tuple(obj.rotation_axis_angle)
        original_scale = tuple(obj.scale)
        saved.append(
            (
                obj,
                original_data,
                original_basis,
                original_rotation_mode,
                original_location,
                original_rotation_euler,
                original_rotation_quaternion,
                original_rotation_axis_angle,
                original_scale,
            )
        )

        original_rotation_scale = _matrix_without_translation(original_basis)
        target_rotation_scale = (
            skinned_node_rotation if _is_skinned_mesh(obj) else identity
        )
        data_bake = target_rotation_scale.inverted() @ original_rotation_scale

        if any(
            abs(float(data_bake[row][column]) - (1.0 if row == column else 0.0))
            > 1e-7
            for row in range(3)
            for column in range(3)
        ):
            obj.data = obj.data.copy()
            try:
                obj.data.transform(data_bake, shape_keys=True)
            except TypeError:
                obj.data.transform(data_bake)
            obj.data.update()

        obj.matrix_basis = (
            Matrix.Translation(Vector(original_basis.translation))
            @ target_rotation_scale
        )

    return saved


def _restore_better_mesh_transforms(saved):
    for (
        obj,
        original_data,
        original_basis,
        original_rotation_mode,
        original_location,
        original_rotation_euler,
        original_rotation_quaternion,
        original_rotation_axis_angle,
        original_scale,
    ) in reversed(saved):
        if obj.name not in bpy.data.objects:
            continue
        temporary_data = obj.data
        obj.rotation_mode = original_rotation_mode
        obj.location = original_location
        if original_rotation_mode == "QUATERNION":
            obj.rotation_quaternion = original_rotation_quaternion
        elif original_rotation_mode == "AXIS_ANGLE":
            obj.rotation_axis_angle = original_rotation_axis_angle
        else:
            obj.rotation_euler = original_rotation_euler
        obj.scale = original_scale
        obj.data = original_data
        if temporary_data != original_data and temporary_data.users == 0:
            bpy.data.meshes.remove(temporary_data)


def _patched_better_execute(self, context):
    global _BETTER_EXPORT_ACTIVE

    original_execute = _BETTER_ORIG_EXECUTE
    if (
        original_execute is None
        or _BETTER_EXPORT_ACTIVE
        or not getattr(
            getattr(context, "scene", None),
            "better_fbx_bake_mesh_transforms",
            False,
        )
    ):
        return original_execute(self, context)

    mesh_objects = _better_export_mesh_objects(context, self)
    if not mesh_objects:
        return original_execute(self, context)

    saved = []
    original_optimize = getattr(self, "use_optimize_for_game_engine", None)
    original_unit = getattr(self, "my_fbx_unit", None)
    scene = getattr(context, "scene", None)
    original_frame = getattr(scene, "frame_current", None)
    original_subframe = getattr(scene, "frame_subframe", 0.0)
    try:
        saved = _bake_better_mesh_transforms(mesh_objects)
        context.view_layer.update()
        if original_optimize:
            # Better's optimized path can reintroduce a mesh-node rotation or
            # rotate non-skinned meshes unexpectedly.  This feature owns the
            # complete Unity transform result, so use the known-good path for
            # this export only and restore the operator setting afterward.
            self.use_optimize_for_game_engine = False
        if original_unit and original_unit != "m":
            # Unity interprets Better FBX's cm unit as a 100x import scale.
            # The Unity mesh-transform option promises scene-unit output, so
            # use meters for this export only and restore the preset afterward.
            self.my_fbx_unit = "m"
        _BETTER_EXPORT_ACTIVE = True
        return original_execute(self, context)
    except Exception as error:
        try:
            self.report({"ERROR"}, f"Mesh transform bake failed: {error}")
        except (AttributeError, RuntimeError):
            pass
        return {"CANCELLED"}
    finally:
        _BETTER_EXPORT_ACTIVE = False
        _restore_better_mesh_transforms(saved)
        if original_optimize is not None:
            try:
                self.use_optimize_for_game_engine = original_optimize
            except (AttributeError, RuntimeError):
                pass
        if original_unit is not None:
            try:
                self.my_fbx_unit = original_unit
            except (AttributeError, RuntimeError):
                pass
        if scene is not None and original_frame is not None:
            scene.frame_set(original_frame, subframe=original_subframe)


def _install_better_patch():
    global _BETTER, _BETTER_ORIG_EXECUTE

    module = _find_better_export_module()
    operator_class = getattr(module, "BetterExportFbx", None) if module else None
    if operator_class is None:
        return False

    current_execute = getattr(operator_class, "execute", None)
    if (
        _BETTER is module
        and _BETTER_ORIG_EXECUTE is not None
        and current_execute is _patched_better_execute
    ):
        return True

    if _BETTER_ORIG_EXECUTE is not None:
        _remove_better_patch()

    _BETTER = module
    _BETTER_ORIG_EXECUTE = current_execute
    operator_class.execute = _patched_better_execute
    return True


def _remove_better_patch():
    global _BETTER, _BETTER_ORIG_EXECUTE, _BETTER_EXPORT_ACTIVE

    _BETTER_EXPORT_ACTIVE = False
    if _BETTER is not None:
        operator_class = getattr(_BETTER, "BetterExportFbx", None)
        if (
            operator_class is not None
            and getattr(operator_class, "execute", None)
            is _patched_better_execute
        ):
            operator_class.execute = _BETTER_ORIG_EXECUTE
    _BETTER = None
    _BETTER_ORIG_EXECUTE = None


class FILEBROWSER_PT_script_toolkit_better_fbx(Panel):
    """Script Toolkit panel shown only in Better FBX's File Browser."""

    bl_idname = "FILEBROWSER_PT_script_toolkit_better_fbx"
    bl_label = "Script Toolkit"
    bl_space_type = "FILE_BROWSER"
    bl_region_type = "TOOL_PROPS"
    bl_parent_id = "FILE_PT_operator"

    @classmethod
    def poll(cls, context):
        operator_id = _operator_identifier(context)
        if operator_id in _BETTER_EXPORT_OPERATOR_IDS:
            _install_better_patch()
            return True
        return False

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        scene = context.scene
        operator = getattr(
            getattr(context, "space_data", None),
            "active_operator",
            None,
        )

        layout.label(text="Better FBX Export Options", icon="EXPORT")
        row = layout.row()
        row.prop(
            scene,
            "better_fbx_bake_mesh_transforms",
            text="Bake Mesh Rotation/Scale for Unity",
        )
        if getattr(operator, "use_optimize_for_game_engine", False):
            layout.label(
                text="Bake will temporarily use Optimize For Game Engine = False",
                icon="INFO",
            )
        else:
            layout.label(
                text="Skinned mesh nodes become rotation 0 and scale 1",
                icon="INFO",
            )


def _patch_retry_timer():
    _install_better_patch()
    return 1.0


def draw_ui(layout, context):
    box = layout.box()
    box.label(text="Better FBX Unity Export", icon="EXPORT")
    box.prop(
        context.scene,
        "better_fbx_bake_mesh_transforms",
        text="Bake Mesh Rotation/Scale for Unity",
    )
    box.label(
        text="Bake temporarily uses Optimize = False and FBX Unit = m",
        icon="INFO",
    )
    box.label(text="Bones are not modified; the source scene is restored.")


CLASSES = (FILEBROWSER_PT_script_toolkit_better_fbx,)


def register():
    _register_scene_properties()
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    _install_better_patch()
    if not bpy.app.timers.is_registered(_patch_retry_timer):
        bpy.app.timers.register(_patch_retry_timer, first_interval=1.0)


def unregister():
    try:
        if bpy.app.timers.is_registered(_patch_retry_timer):
            bpy.app.timers.unregister(_patch_retry_timer)
    except (AttributeError, RuntimeError, ValueError):
        pass
    _remove_better_patch()
    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except (RuntimeError, TypeError):
            pass
    _unregister_scene_properties()
