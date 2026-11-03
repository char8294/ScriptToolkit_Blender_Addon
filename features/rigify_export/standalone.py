"""Embed the exporter and its profile data inside a Blender file."""

import json
from pathlib import Path

import bpy


IS_EMBEDDED = False
BOOTSTRAP_TEXT = "REX_Standalone_Exporter.py"
STATE_TEXT = "REX_Standalone_Exporter_State"
SOURCE_TEXTS = {
    "properties": "REX_Standalone_properties.py",
    "core": "REX_Standalone_core.py",
    "standalone": "REX_Standalone_standalone.py",
    "ui": "REX_Standalone_ui.py",
}
PACKAGE_NAME = "_rex_embedded_exporter"
STATE_VERSION = 1


BOOTSTRAP_SOURCE = r'''import bpy
import sys
import traceback
import types

_rex_package_name = "_rex_embedded_exporter"
_rex_package = sys.modules.get(_rex_package_name)
if bpy.app.driver_namespace.get("rex_export_main_addon_active"):
    print("[Rigify Export] Embedded exporter skipped because the main add-on is active.")
elif hasattr(bpy.types.Object, "rex_profiles") and _rex_package:
    try:
        _rex_package.standalone.restore_embedded_state()
        _rex_package.ui._load_post(None)
        print("[Rigify Export] Reused the embedded exporter for this .blend file.")
    except Exception:
        traceback.print_exc()
elif hasattr(bpy.types.Object, "rex_profiles"):
    print("[Rigify Export] Export properties are already registered by another module.")
else:
    try:
        _rex_package = types.ModuleType(_rex_package_name)
        _rex_package.__path__ = []
        sys.modules[_rex_package_name] = _rex_package
        for _rex_module_name in ("properties", "core", "standalone", "ui"):
            _rex_module = types.ModuleType(_rex_package_name + "." + _rex_module_name)
            _rex_module.__package__ = _rex_package_name
            _rex_module.__file__ = "<embedded Rigify Export: " + _rex_module_name + ">"
            sys.modules[_rex_module.__name__] = _rex_module
            setattr(_rex_package, _rex_module_name, _rex_module)
            _rex_text_name = {
                "properties": "REX_Standalone_properties.py",
                "core": "REX_Standalone_core.py",
                "standalone": "REX_Standalone_standalone.py",
                "ui": "REX_Standalone_ui.py",
            }[_rex_module_name]
            _rex_text = bpy.data.texts.get(_rex_text_name)
            if _rex_text is None:
                raise RuntimeError("Missing embedded module: " + _rex_text_name)
            exec(compile(_rex_text.as_string(), _rex_text_name, "exec"), _rex_module.__dict__)
        _rex_package.standalone.IS_EMBEDDED = True
        _rex_package.properties.register()
        _rex_package.standalone.restore_embedded_state()
        _rex_package.standalone.register_save_handler()
        _rex_package.ui.register(register_panels=True)
        print("[Rigify Export] Embedded exporter registered from this .blend file.")
    except Exception:
        traceback.print_exc()
'''


def _action_to_state(item):
    return {
        "action": item.action.name if item.action else "",
        "slot": item.action_slot_identifier,
        "user_added": bool(item.user_added),
        "enabled": bool(item.enabled),
        "use_baked_action_name": bool(item.use_baked_action_name),
        "baked_action_name": item.baked_action_name,
        "frame_range_mode": item.frame_range_mode,
        "set_frame_start": int(item.set_frame_start),
        "set_frame_end": int(item.set_frame_end),
        "trim_start": int(item.trim_start),
        "trim_end": int(item.trim_end),
    }


def _profile_to_state(profile):
    return {
        "uid": profile.uid,
        "name": profile.name,
        "control_rig": profile.control_rig.name if profile.control_rig else "",
        "hierarchy_mode": profile.hierarchy_mode,
        "disconnect_bones": bool(profile.disconnect_bones),
        "include_mesh": bool(profile.include_mesh),
        "pack_actions_into_nla": bool(
            getattr(profile, "pack_actions_into_nla", False)
        ),
        "pack_file_name": getattr(profile, "pack_file_name", ""),
        "output_directory": profile.output_directory,
        "better_fbx_preset": getattr(profile, "better_fbx_preset", "NONE"),
        "source_signature": profile.source_signature,
        "export_bones": [
            {"name": bone.name, "parent_name": bone.parent_name}
            for bone in profile.export_bones
        ],
        "action_items": [_action_to_state(item) for item in profile.action_items if item.user_added],
        "mesh_items": [
            {"mesh": item.mesh.name if item.mesh else "", "enabled": bool(item.enabled)}
            for item in profile.mesh_items
        ],
    }


def snapshot_scene(scene):
    rigs = []
    profile_count = 0
    for rig in bpy.data.objects:
        if (
            rig.type != "ARMATURE"
            or not rig.data
            or not rig.data.get("rig_id")
            or not hasattr(rig, "rex_profiles")
        ):
            continue
        profiles = [
            _profile_to_state(profile)
            for profile in rig.rex_profiles
            if profile.configuration_valid and profile.export_bones
        ]
        if not profiles:
            continue
        rigs.append({"name": rig.name, "profiles": profiles})
        profile_count += len(profiles)

    control_rig = getattr(scene, "rex_control_rig", None) if scene else None
    if not control_rig or not control_rig.data or not control_rig.data.get("rig_id"):
        control_rig = None
    return {
        "version": STATE_VERSION,
        "control_rig": control_rig.name if control_rig else "",
        "rigs": rigs,
        "profile_count": profile_count,
    }


def embedded_profile_entries():
    """Return profile records saved in the internal exporter state text block."""
    bootstrap = bpy.data.texts.get(BOOTSTRAP_TEXT)
    state_text = bpy.data.texts.get(STATE_TEXT)
    if not bootstrap or not bootstrap.use_module or not state_text:
        return []
    try:
        state = json.loads(state_text.as_string())
    except (TypeError, ValueError):
        return []
    if state.get("version") != STATE_VERSION:
        return []
    entries = []
    seen = set()
    for rig_data in state.get("rigs", ()):
        rig = bpy.data.objects.get(rig_data.get("name", ""))
        if not rig or rig.type != "ARMATURE" or not rig.data or not rig.data.get("rig_id"):
            continue
        for profile_data in rig_data.get("profiles", ()):
            uid = profile_data.get("uid", "")
            if not uid or uid in seen:
                continue
            seen.add(uid)
            entries.append(
                {
                    "uid": uid,
                    "name": profile_data.get("name", "Character Export"),
                    "rig_name": rig_data.get("name", ""),
                    "data": profile_data,
                }
            )
    return entries


def _replace_text(name, content):
    text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    text.clear()
    text.write(content)
    return text


def embed_file(scene):
    package_dir = Path(__file__).resolve().parent
    source_files = {
        "properties": package_dir / "properties.py",
        "core": package_dir / "core.py",
        "standalone": package_dir / "standalone.py",
        "ui": package_dir / "ui.py",
    }
    state = snapshot_scene(scene)
    if not state["profile_count"]:
        raise RuntimeError("Save to UI at least one valid profile before embedding the exporter.")

    for key, source_path in source_files.items():
        _replace_text(SOURCE_TEXTS[key], source_path.read_text(encoding="utf-8"))
    _replace_text(STATE_TEXT, json.dumps(state, ensure_ascii=False, indent=2))
    bootstrap = _replace_text(BOOTSTRAP_TEXT, BOOTSTRAP_SOURCE)
    bootstrap.use_module = True
    return state["profile_count"]


def sync_embedded_file(scene):
    """Refresh the packed exporter after saved profiles are created or deleted."""
    state = snapshot_scene(scene)
    if state["profile_count"]:
        return embed_file(scene)
    for text_name in (BOOTSTRAP_TEXT, STATE_TEXT, *SOURCE_TEXTS.values()):
        text = bpy.data.texts.get(text_name)
        if text:
            bpy.data.texts.remove(text)
    return 0


def _restore_profile(rig, data):
    profile = rig.rex_profiles.add()
    profile.uid = data.get("uid", "")
    profile.name = data.get("name", "Character Export")
    profile.control_rig = rig
    profile.game_rig = None
    profile.hierarchy_mode = data.get("hierarchy_mode", "KEEP_EXISTING")
    profile.disconnect_bones = bool(data.get("disconnect_bones", True))
    profile.include_mesh = bool(data.get("include_mesh", False))
    profile.pack_actions_into_nla = bool(
        data.get("pack_actions_into_nla", False)
    )
    profile.pack_file_name = data.get("pack_file_name") or (
        profile.name + "_Animations"
    )
    profile.mesh_list_initialized = True
    profile.output_directory = data.get("output_directory", "")
    profile.better_fbx_preset = data.get("better_fbx_preset") or "NONE"
    profile.source_signature = data.get("source_signature", "")
    profile.configuration_valid = True
    profile.attached = False

    for bone_data in data.get("export_bones", ()):
        bone = profile.export_bones.add()
        bone.name = bone_data.get("name", "")
        bone.parent_name = bone_data.get("parent_name", "")

    for action_data in data.get("action_items", ()):
        item = profile.action_items.add()
        item.action = bpy.data.actions.get(action_data.get("action", ""))
        item.action_slot_identifier = action_data.get("slot", "")
        item.user_added = bool(action_data.get("user_added", True))
        item.enabled = bool(action_data.get("enabled", True))
        item.use_baked_action_name = bool(action_data.get("use_baked_action_name", False))
        item.baked_action_name = action_data.get("baked_action_name", "")
        if item.action:
            item.frame_range_mode = action_data.get("frame_range_mode", "ACTION")
            item.set_frame_range_initialized = True
        item.set_frame_start = int(action_data.get("set_frame_start", 0))
        item.set_frame_end = int(action_data.get("set_frame_end", 1))
        item.trim_start = int(action_data.get("trim_start", 0))
        item.trim_end = int(action_data.get("trim_end", 0))

    for mesh_data in data.get("mesh_items", ()):
        item = profile.mesh_items.add()
        item.mesh = bpy.data.objects.get(mesh_data.get("mesh", ""))
        item.enabled = bool(mesh_data.get("enabled", True))
    return profile


def restore_embedded_state():
    text = bpy.data.texts.get(STATE_TEXT)
    if not text:
        return 0
    state = json.loads(text.as_string())
    if state.get("version") != STATE_VERSION:
        raise RuntimeError("This embedded exporter state uses an unsupported version.")

    restored = 0
    existing_uids = {
        profile.uid
        for candidate in bpy.data.objects
        if (
            candidate.type == "ARMATURE"
            and candidate.data
            and candidate.data.get("rig_id")
            and hasattr(candidate, "rex_profiles")
        )
        for profile in candidate.rex_profiles
        if profile.uid
    }
    for rig_data in state.get("rigs", ()):
        rig = bpy.data.objects.get(rig_data.get("name", ""))
        if not rig or rig.type != "ARMATURE" or not rig.data or not rig.data.get("rig_id"):
            continue
        for profile_data in rig_data.get("profiles", ()):
            uid = profile_data.get("uid", "")
            if not uid or uid in existing_uids:
                continue
            _restore_profile(rig, profile_data)
            existing_uids.add(uid)
            restored += 1
        if rig.rex_profiles:
            if not getattr(rig, "rex_profile_dropdown", "") or rig.rex_profile_dropdown == "NONE":
                rig.rex_profile_index = 0
                rig.rex_profile_dropdown = rig.rex_profiles[0].uid

    control_rig = bpy.data.objects.get(state.get("control_rig", ""))
    if control_rig and control_rig.type == "ARMATURE" and control_rig.data and control_rig.data.get("rig_id"):
        bpy.context.scene.rex_control_rig = control_rig
    return restored


@bpy.app.handlers.persistent
def _save_embedded_state(_unused):
    text = bpy.data.texts.get(STATE_TEXT)
    if not text or not hasattr(bpy.types.Object, "rex_profiles"):
        return
    try:
        state = snapshot_scene(bpy.context.scene)
        text.clear()
        text.write(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as error:
        print("[Rigify Export] Could not update embedded profile data:", error)


def register_save_handler():
    if _save_embedded_state not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_save_embedded_state)


def unregister_save_handler():
    if _save_embedded_state in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_save_embedded_state)
