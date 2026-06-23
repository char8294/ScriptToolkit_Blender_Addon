bl_info = {
    "name": "Script Toolkit",
    "author": "Smart Office + Codex",
    "version": (0, 3, 0),
    "blender": (5, 1, 0),
    "location": "3D View > Sidebar > Script Toolkit",
    "description": "FBX batch tools in an isolated Blender worker plus selected-object cleanup tools.",
    "category": "Import-Export",
}

import json
import os
import re
import subprocess
import tempfile
import textwrap
import urllib.error
import urllib.request
import uuid

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import biped_names, hair_check


# GitHub update configuration — follows the Turntable Camera updater pattern.
GITHUB_OWNER = "char8294"
GITHUB_REPO = "Pack_Blender_Add-on"
GITHUB_ADDON_FOLDER = "script_toolkit"
GITHUB_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/{GITHUB_ADDON_FOLDER}/__init__.py"
GITHUB_API_CONTENTS = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_ADDON_FOLDER}"
GITHUB_CHANGELOG_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/{GITHUB_ADDON_FOLDER}/CHANGELOG.md"

_update_info = {
    "checked": False,
    "has_update": False,
    "current_version": (0, 0, 0),
    "latest_version": (0, 0, 0),
    "error": "",
    "changelog": [],
}


BATCH_TOOLS = {"REEXPORT", "OVERLAP", "MATERIAL_CLEANUP", "SEPARATE"}


def _abs_path(path):
    return os.path.abspath(bpy.path.abspath(path)) if path else ""


def _job_directory():
    directory = os.path.join(tempfile.gettempdir(), "script_toolkit_jobs")
    os.makedirs(directory, exist_ok=True)
    return directory


def _find_fbx_files(folder, recursive):
    if not os.path.isdir(folder):
        return []
    if recursive:
        return [
            os.path.join(root, name)
            for root, _, names in os.walk(folder)
            for name in names if name.lower().endswith(".fbx")
        ]
    return [
        os.path.join(folder, name) for name in os.listdir(folder)
        if name.lower().endswith(".fbx")
    ]


def _tool_description(tool):
    return {
        "REEXPORT": "Import FBX ปรับ smooth/normals/scale ตามที่เลือก แล้ว export ใหม่ใน Blender worker.",
        "OVERLAP": "ใส่ vertex weight ให้จุดที่อยู่ภายใน SkinVolume โดยรันใน Blender worker.",
        "MATERIAL_CLEANUP": "จัด material slot, bone และ normals ของ FBX เป็นชุดใน Blender worker.",
        "SEPARATE": "แยก mesh ตาม material และตั้งชื่อ/ล้าง edge data ใน Blender worker.",
        "DELETE_FACES": "ลบ faces ของ material prefix จาก mesh ที่เลือกในไฟล์ปัจจุบัน.",
        "CLEAR_PROPS": "ลบ custom properties จาก object ที่เลือกในไฟล์ปัจจุบัน.",
        "HAIR_CHECK": "Cycle hair objects ทีละชิ้น โดยตรึง Hat Object ให้แสดงอยู่ ตามรูปแบบ Check Hair And Cap เดิม.",
        "BIPED_NAMES": "เปลี่ยนชื่อ Biped bones/vertex groups เพื่อใช้ Symmetry และคืนชื่อเดิม ตามรูปแบบ Biped Names Helper เดิม.",
    }[tool]


class ST_Properties(PropertyGroup):
    tool: EnumProperty(
        name="Tool",
        items=[
            ("REEXPORT", "Re-export FBX", "Import, cleanup และ export FBX ใหม่"),
            ("OVERLAP", "Skin Volume Weight", "กำหนด weight จาก volume"),
            ("MATERIAL_CLEANUP", "Material ID Cleanup", "จัด material, bone และ normals"),
            ("SEPARATE", "Separate by Material", "แยก mesh ตาม material"),
            ("DELETE_FACES", "Delete Faces by Material", "ลบ faces ของ material จาก selection"),
            ("CLEAR_PROPS", "Clear Custom Properties", "ลบ metadata จาก selection"),
            ("HAIR_CHECK", "Check Hair And Cap", "Cycle hair objects while keeping the hat visible"),
            ("BIPED_NAMES", "Biped Names Helper", "Convert Biped names for symmetry and restore them"),
        ],
        default="REEXPORT",
    )

    # Shared batch settings
    input_dir: StringProperty(name="Input Folder", subtype="DIR_PATH")
    output_dir: StringProperty(name="Output Folder", subtype="DIR_PATH")
    include_subfolders: BoolProperty(name="Include Subfolders", default=True)
    keep_folder_structure: BoolProperty(name="Keep Folder Structure", default=True)
    overwrite_existing: BoolProperty(name="Overwrite Existing Files", default=True)
    background_worker: BoolProperty(
        name="Background Worker",
        description="เปิด Blender worker แบบไม่มีหน้าต่าง; ปิดเพื่อดูหน้าต่าง worker สำหรับ debug",
        default=True,
    )

    # Re-export
    shade_smooth: BoolProperty(name="Shade Smooth", default=True)
    clear_sharp_edges: BoolProperty(name="Clear Sharp Edges", default=True)
    clear_custom_normals: BoolProperty(name="Clear Custom Split Normals", default=True)
    apply_scale: BoolProperty(name="Apply Scale", default=False)
    disable_leaf_bones: BoolProperty(name="Disable Leaf Bones", default=True)

    # Skin volume weight
    skin_volume_path: StringProperty(name="SkinVolume FBX", subtype="FILE_PATH")
    armature_name: StringProperty(name="Armature Name", default="Bip001")
    skin_volume_keyword: StringProperty(name="SkinVolume Object Keyword", default="SkinVolume")
    target_vertex_group: StringProperty(name="Target Vertex Group", default="Bip001 Neck")
    weight_value: FloatProperty(name="Weight Value", default=1.0, min=0.0, max=1.0)
    auto_normalize: BoolProperty(name="Auto Normalize Other Weights", default=True)
    overlap_output_name: EnumProperty(
        name="Output Name",
        items=[("SOURCE", "Source Filename", "ใช้ชื่อไฟล์ต้นฉบับ"), ("MESH", "First Mesh Name", "ใช้ชื่อ mesh ตัวแรก")],
        default="SOURCE",
    )

    # Material cleanup
    material_keyword: StringProperty(name="Material Keyword", default="SKIN_body")
    cleanup_remove_empty: BoolProperty(name="Remove Empty Objects", default=True)
    cleanup_remove_unused_bones: BoolProperty(name="Remove Unused Bones", default=True)
    cleanup_set_slot_zero: BoolProperty(name="Move Keyword Material to Slot 0", default=True)
    cleanup_sort_faces: BoolProperty(name="Sort Faces by Material", default=True)
    cleanup_shade_smooth: BoolProperty(name="Shade Smooth", default=True)
    cleanup_clear_sharp: BoolProperty(name="Clear Sharp Edges", default=True)
    cleanup_clear_normals: BoolProperty(name="Clear Custom Normals", default=True)

    # Separate by material
    separate_min_materials: IntProperty(name="Minimum Material Count", default=2, min=2, max=64)
    separate_clean_names: BoolProperty(name="Clean _mat / .001 Suffixes", default=True)
    separate_rename_data: BoolProperty(name="Rename Object, Mesh and Material", default=True)
    separate_shade_smooth: BoolProperty(name="Shade Smooth", default=True)
    separate_clear_normals: BoolProperty(name="Clear Custom Normals", default=True)
    separate_clear_edge_marks: BoolProperty(name="Clear Sharp, Seam, Crease and Bevel", default=True)

    # Current scene tools
    delete_material_prefix: StringProperty(name="Material Prefix", default="SKIN_body")
    delete_remove_slots: BoolProperty(name="Remove Matching Material Slots", default=True)
    clear_object_properties: BoolProperty(name="Clear Object Properties", default=True)
    clear_data_properties: BoolProperty(name="Clear Mesh Data Properties", default=True)

    # Runtime status
    last_job_path: StringProperty(options={"HIDDEN"})
    last_result_path: StringProperty(options={"HIDDEN"})
    last_status: StringProperty(name="Status", default="Ready")
    last_summary: StringProperty(name="Summary", default="")


def _batch_payload(props):
    return {
        "tool": props.tool,
        "input_dir": _abs_path(props.input_dir),
        "output_dir": _abs_path(props.output_dir),
        "include_subfolders": props.include_subfolders,
        "keep_folder_structure": props.keep_folder_structure,
        "overwrite_existing": props.overwrite_existing,
        "disable_leaf_bones": props.disable_leaf_bones,
        "reexport": {
            "shade_smooth": props.shade_smooth,
            "clear_sharp_edges": props.clear_sharp_edges,
            "clear_custom_normals": props.clear_custom_normals,
            "apply_scale": props.apply_scale,
        },
        "overlap": {
            "skin_volume_path": _abs_path(props.skin_volume_path),
            "armature_name": props.armature_name,
            "skin_volume_keyword": props.skin_volume_keyword,
            "target_vertex_group": props.target_vertex_group,
            "weight_value": props.weight_value,
            "auto_normalize": props.auto_normalize,
            "output_name": props.overlap_output_name,
        },
        "material_cleanup": {
            "material_keyword": props.material_keyword,
            "remove_empty": props.cleanup_remove_empty,
            "remove_unused_bones": props.cleanup_remove_unused_bones,
            "set_slot_zero": props.cleanup_set_slot_zero,
            "sort_faces": props.cleanup_sort_faces,
            "shade_smooth": props.cleanup_shade_smooth,
            "clear_sharp_edges": props.cleanup_clear_sharp,
            "clear_custom_normals": props.cleanup_clear_normals,
        },
        "separate": {
            "minimum_materials": props.separate_min_materials,
            "clean_names": props.separate_clean_names,
            "rename_data": props.separate_rename_data,
            "shade_smooth": props.separate_shade_smooth,
            "clear_custom_normals": props.separate_clear_normals,
            "clear_edge_marks": props.separate_clear_edge_marks,
        },
    }


def _validate_batch(props):
    input_dir = _abs_path(props.input_dir)
    output_dir = _abs_path(props.output_dir)
    if not input_dir or not os.path.isdir(input_dir):
        return False, "Input Folder ไม่พบหรือยังไม่ได้เลือก"
    if not output_dir:
        return False, "กรุณาเลือก Output Folder"
    if os.path.normcase(input_dir) == os.path.normcase(output_dir):
        return False, "Input Folder และ Output Folder ต้องไม่เป็นโฟลเดอร์เดียวกัน"
    if props.tool == "OVERLAP":
        skin_volume = _abs_path(props.skin_volume_path)
        if not skin_volume or not os.path.isfile(skin_volume):
            return False, "SkinVolume FBX ไม่พบหรือยังไม่ได้เลือก"
    files = _find_fbx_files(input_dir, props.include_subfolders)
    if not files:
        return False, "ไม่พบไฟล์ FBX ใน Input Folder"
    return True, f"พร้อมประมวลผล {len(files)} FBX file(s)"


class ST_OT_validate_batch(Operator):
    bl_idname = "script_toolkit.validate_batch"
    bl_label = "Validate"

    def execute(self, context):
        props = context.scene.script_toolkit
        valid, message = _validate_batch(props)
        props.last_status = message
        self.report({"INFO" if valid else "ERROR"}, message)
        return {"FINISHED" if valid else "CANCELLED"}


class ST_OT_start_batch(Operator):
    bl_idname = "script_toolkit.start_batch"
    bl_label = "Start Batch"

    def execute(self, context):
        props = context.scene.script_toolkit
        valid, message = _validate_batch(props)
        if not valid:
            self.report({"ERROR"}, message)
            return {"CANCELLED"}

        job_id = uuid.uuid4().hex
        job_dir = _job_directory()
        job_path = os.path.join(job_dir, f"job_{job_id}.json")
        result_path = os.path.join(job_dir, f"result_{job_id}.json")
        payload = _batch_payload(props)
        payload.update({"job_id": job_id, "result_path": result_path})

        with open(job_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump({"status": "queued", "message": "Worker is starting", "total": 0, "success": 0, "failed": []}, handle)

        worker_script = os.path.join(os.path.dirname(__file__), "worker_entry.py")
        command = [bpy.app.binary_path]
        if props.background_worker:
            command.append("--background")
        command.extend(["--factory-startup", "--python", worker_script, "--", job_path])
        popen_args = {}
        if os.name == "nt" and props.background_worker:
            popen_args["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            subprocess.Popen(command, **popen_args)
        except OSError as exc:
            props.last_status = f"เปิด Blender worker ไม่สำเร็จ: {exc}"
            self.report({"ERROR"}, props.last_status)
            return {"CANCELLED"}

        props.last_job_path = job_path
        props.last_result_path = result_path
        props.last_status = "Worker started — กด Refresh Status เพื่อตรวจผล"
        props.last_summary = ""
        self.report({"INFO"}, "Blender worker started")
        return {"FINISHED"}


class ST_OT_refresh_status(Operator):
    bl_idname = "script_toolkit.refresh_status"
    bl_label = "Refresh Status"

    def execute(self, context):
        props = context.scene.script_toolkit
        if not props.last_result_path or not os.path.isfile(props.last_result_path):
            self.report({"WARNING"}, "ยังไม่มี job result")
            return {"CANCELLED"}
        try:
            with open(props.last_result_path, "r", encoding="utf-8") as handle:
                result = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            self.report({"WARNING"}, f"อ่านสถานะไม่ได้: {exc}")
            return {"CANCELLED"}
        status = result.get("status", "unknown")
        success = result.get("success", 0)
        total = result.get("total", 0)
        failed = result.get("failed", [])
        props.last_status = f"{status}: {result.get('message', '')}"
        props.last_summary = f"Success {success}/{total}; Failed {len(failed)}"
        self.report({"INFO" if status in {"running", "completed"} else "ERROR"}, props.last_summary)
        return {"FINISHED"}


class ST_OT_delete_faces(Operator):
    bl_idname = "script_toolkit.delete_faces_by_material"
    bl_label = "Delete Faces by Material"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        self.prefix = context.scene.script_toolkit.delete_material_prefix
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        props = context.scene.script_toolkit
        prefix = props.delete_material_prefix.strip().lower()
        if not prefix:
            self.report({"ERROR"}, "Material Prefix ต้องไม่ว่าง")
            return {"CANCELLED"}
        deleted_total, objects_modified = 0, 0
        for obj in list(context.selected_objects):
            if obj.type != "MESH":
                continue
            indices = [
                index for index, slot in enumerate(obj.material_slots)
                if slot.material and slot.material.name.lower().startswith(prefix)
            ]
            if not indices:
                continue
            objects_modified += 1
            bpy.context.view_layer.objects.active = obj
            if obj.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.mode_set(mode="EDIT")
            mesh = obj.data
            edit_mesh = bmesh.from_edit_mesh(mesh)
            faces = [face for face in edit_mesh.faces if face.material_index in indices]
            deleted_total += len(faces)
            if faces:
                bmesh.ops.delete(edit_mesh, geom=faces, context="FACES")
                bmesh.update_edit_mesh(mesh)
            bpy.ops.object.mode_set(mode="OBJECT")
            if props.delete_remove_slots:
                for index in reversed(range(len(obj.material_slots))):
                    slot = obj.material_slots[index]
                    if slot.material and slot.material.name.lower().startswith(prefix):
                        obj.active_material_index = index
                        bpy.ops.object.material_slot_remove()
        props.last_status = f"Deleted {deleted_total} faces in {objects_modified} object(s)"
        self.report({"INFO"}, props.last_status)
        return {"FINISHED"}


class ST_OT_clear_properties(Operator):
    bl_idname = "script_toolkit.clear_custom_properties"
    bl_label = "Clear Custom Properties"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.script_toolkit
        if not context.selected_objects:
            self.report({"ERROR"}, "กรุณาเลือก object อย่างน้อยหนึ่งชิ้น")
            return {"CANCELLED"}
        object_count, data_count = 0, 0
        for obj in context.selected_objects:
            if props.clear_object_properties:
                for key in [key for key in obj.keys() if key != "_RNA_UI"]:
                    del obj[key]
                    object_count += 1
            if props.clear_data_properties and obj.data and hasattr(obj.data, "keys"):
                for key in [key for key in obj.data.keys() if key != "_RNA_UI"]:
                    del obj.data[key]
                    data_count += 1
        props.last_status = f"Removed {object_count} object and {data_count} data properties"
        self.report({"INFO"}, props.last_status)
        return {"FINISHED"}


class ST_OT_check_update(Operator):
    bl_idname = "script_toolkit.check_update"
    bl_label = "Check for Updates"

    def execute(self, context):
        _update_info.update({
            "checked": False,
            "has_update": False,
            "current_version": bl_info["version"],
            "latest_version": (0, 0, 0),
            "error": "",
            "changelog": [],
        })
        try:
            request = urllib.request.Request(GITHUB_RAW_URL, headers={"User-Agent": "Blender-Script-Toolkit-Updater"})
            with urllib.request.urlopen(request, timeout=10) as response:
                content = response.read().decode("utf-8")
            match = re.search(r'"version"\s*:\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', content)
            if not match:
                raise RuntimeError("ไม่สามารถอ่านเวอร์ชันจาก GitHub ได้")
            latest = tuple(int(match.group(index)) for index in range(1, 4))
            _update_info["latest_version"] = latest
            _update_info["has_update"] = latest > bl_info["version"]
            _update_info["checked"] = True

            if _update_info["has_update"]:
                try:
                    request = urllib.request.Request(GITHUB_CHANGELOG_URL, headers={"User-Agent": "Blender-Script-Toolkit-Updater"})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        changelog = response.read().decode("utf-8")
                    lines = []
                    for line in changelog.splitlines():
                        line = line.strip()
                        if line:
                            lines.extend(textwrap.wrap(line, width=46, break_long_words=False) or [line])
                    _update_info["changelog"] = lines[:15]
                except Exception:
                    pass
        except urllib.error.URLError as exc:
            _update_info["error"] = f"ไม่สามารถเชื่อมต่อ: {exc.reason}"
            _update_info["checked"] = True
        except Exception as exc:
            _update_info["error"] = str(exc)
            _update_info["checked"] = True

        bpy.ops.script_toolkit.update_popup("INVOKE_DEFAULT")
        return {"FINISHED"}


class ST_OT_update_popup(Operator):
    bl_idname = "script_toolkit.update_popup"
    bl_label = "Script Toolkit — Update"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=330)

    def draw(self, context):
        layout = self.layout
        if _update_info["error"]:
            layout.label(text="เกิดข้อผิดพลาด", icon="ERROR")
            for line in textwrap.wrap(_update_info["error"], width=48):
                layout.label(text=line)
            return
        if not _update_info["checked"]:
            layout.label(text="ยังไม่ได้ตรวจสอบ", icon="INFO")
            return
        current = ".".join(str(value) for value in _update_info["current_version"])
        latest = ".".join(str(value) for value in _update_info["latest_version"])
        layout.label(text=f"เวอร์ชันปัจจุบัน: v{current}", icon="PACKAGE")
        layout.label(text=f"เวอร์ชันล่าสุด: v{latest}", icon="WORLD")
        layout.separator()
        if _update_info["has_update"]:
            box = layout.box()
            box.label(text="มีเวอร์ชันใหม่!", icon="INFO")
            if _update_info["changelog"]:
                box.separator()
                box.label(text="What's New:", icon="TEXT")
                for line in _update_info["changelog"]:
                    box.label(text=line)
            box.separator()
            box.label(text="อัปเดตเสร็จแล้วให้ Restart Blender", icon="ERROR")
            box.label(text="หรือกด F3 แล้วเลือก Reload Scripts")
            box.operator("script_toolkit.do_update", text="Update Now", icon="IMPORT")
        else:
            layout.label(text="เป็นเวอร์ชันล่าสุดแล้ว", icon="CHECKMARK")

    def execute(self, context):
        return {"FINISHED"}


class ST_OT_do_update(Operator):
    bl_idname = "script_toolkit.do_update"
    bl_label = "Update Script Toolkit"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            request = urllib.request.Request(GITHUB_API_CONTENTS, headers={"User-Agent": "Blender-Script-Toolkit-Updater"})
            with urllib.request.urlopen(request, timeout=15) as response:
                files = json.loads(response.read().decode("utf-8"))
            addon_path = os.path.join(bpy.utils.user_resource("SCRIPTS"), "addons", GITHUB_ADDON_FOLDER)
            os.makedirs(addon_path, exist_ok=True)
            updated_count = 0
            for file_info in files:
                if file_info.get("type") != "file" or not file_info.get("download_url"):
                    continue
                request = urllib.request.Request(file_info["download_url"], headers={"User-Agent": "Blender-Script-Toolkit-Updater"})
                with urllib.request.urlopen(request, timeout=15) as response:
                    content = response.read()
                with open(os.path.join(addon_path, file_info["name"]), "wb") as handle:
                    handle.write(content)
                updated_count += 1
            self.report({"INFO"}, f"อัปเดตเสร็จ! ({updated_count} ไฟล์) กรุณา Restart Blender หรือ Reload Scripts")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, f"อัปเดตล้มเหลว: {exc}")
            return {"CANCELLED"}


class ST_PT_panel(Panel):
    bl_label = "Script Toolkit"
    bl_idname = "ST_PT_script_toolkit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Script Toolkit"

    def draw(self, context):
        layout = self.layout
        props = context.scene.script_toolkit
        tool_row = layout.row(align=True)
        tool_row.prop(props, "tool")
        tool_row.operator("script_toolkit.check_update", text="", icon="WORLD")
        help_box = layout.box()
        description_lines = textwrap.wrap(
            _tool_description(props.tool),
            width=48,
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
        for index, line in enumerate(description_lines):
            help_box.label(text=line, icon="INFO" if index == 0 else "BLANK1")

        if props.tool in BATCH_TOOLS:
            self._draw_batch(layout, props)
        elif props.tool == "DELETE_FACES":
            box = layout.box()
            box.label(text="Current selection", icon="RESTRICT_SELECT_OFF")
            box.prop(props, "delete_material_prefix")
            box.prop(props, "delete_remove_slots")
            box.operator("script_toolkit.delete_faces_by_material", icon="TRASH")
        elif props.tool == "CLEAR_PROPS":
            box = layout.box()
            box.label(text="Current selection", icon="RESTRICT_SELECT_OFF")
            box.prop(props, "clear_object_properties")
            box.prop(props, "clear_data_properties")
            box.operator("script_toolkit.clear_custom_properties", icon="TRASH")
        elif props.tool == "HAIR_CHECK":
            hair_check.draw_ui(layout, context)
        else:
            biped_names.draw_ui(layout, context)

        status = layout.box()
        status.label(text="Status", icon="INFO")
        status.label(text=props.last_status)
        if props.last_summary:
            status.label(text=props.last_summary)

    def _draw_batch(self, layout, props):
        paths = layout.box()
        paths.label(text="Batch Worker", icon="FILE_FOLDER")
        paths.prop(props, "background_worker")
        paths.prop(props, "input_dir")
        paths.prop(props, "output_dir")
        paths.prop(props, "include_subfolders")
        paths.prop(props, "keep_folder_structure")
        paths.prop(props, "overwrite_existing")

        options = layout.box()
        if props.tool == "REEXPORT":
            options.label(text="Geometry and FBX Export", icon="MODIFIER")
            options.prop(props, "shade_smooth")
            options.prop(props, "clear_sharp_edges")
            options.prop(props, "clear_custom_normals")
            options.prop(props, "apply_scale")
            options.prop(props, "disable_leaf_bones")
        elif props.tool == "OVERLAP":
            options.label(text="Skin Volume Weight", icon="MOD_VERTEX_WEIGHT")
            options.prop(props, "skin_volume_path")
            options.prop(props, "armature_name")
            options.prop(props, "skin_volume_keyword")
            options.prop(props, "target_vertex_group")
            options.prop(props, "weight_value")
            options.prop(props, "auto_normalize")
            options.prop(props, "overlap_output_name")
            options.prop(props, "disable_leaf_bones")
        elif props.tool == "MATERIAL_CLEANUP":
            options.label(text="Material ID Cleanup", icon="MATERIAL")
            options.prop(props, "material_keyword")
            options.prop(props, "cleanup_remove_empty")
            options.prop(props, "cleanup_remove_unused_bones")
            options.prop(props, "cleanup_set_slot_zero")
            options.prop(props, "cleanup_sort_faces")
            options.prop(props, "cleanup_shade_smooth")
            options.prop(props, "cleanup_clear_sharp")
            options.prop(props, "cleanup_clear_normals")
            options.prop(props, "disable_leaf_bones")
        else:
            options.label(text="Separate by Material", icon="MOD_EXPLODE")
            options.prop(props, "separate_min_materials")
            options.prop(props, "separate_clean_names")
            options.prop(props, "separate_rename_data")
            options.prop(props, "separate_shade_smooth")
            options.prop(props, "separate_clear_normals")
            options.prop(props, "separate_clear_edge_marks")
            options.prop(props, "disable_leaf_bones")

        row = layout.row(align=True)
        row.operator("script_toolkit.validate_batch", icon="CHECKMARK")
        row.operator("script_toolkit.start_batch", icon="PLAY")
        layout.operator("script_toolkit.refresh_status", icon="FILE_REFRESH")


CLASSES = (
    ST_Properties,
    ST_OT_validate_batch,
    ST_OT_start_batch,
    ST_OT_refresh_status,
    ST_OT_delete_faces,
    ST_OT_clear_properties,
    ST_OT_check_update,
    ST_OT_update_popup,
    ST_OT_do_update,
    ST_PT_panel,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.script_toolkit = bpy.props.PointerProperty(type=ST_Properties)
    hair_check.register()
    biped_names.register()


def unregister():
    biped_names.unregister()
    hair_check.unregister()
    del bpy.types.Scene.script_toolkit
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
