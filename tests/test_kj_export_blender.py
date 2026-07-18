import importlib.util
import sys
from pathlib import Path

import bpy


def load_addon():
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "script_toolkit",
        repo_root / "__init__.py",
        submodule_search_locations=[str(repo_root)],
    )
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    spec.loader.exec_module(addon)
    return addon


def run():
    addon = load_addon()
    addon.register()
    try:
        scene = bpy.context.scene
        assert hasattr(scene, "batch_better_fbx_props")
        assert hasattr(bpy.ops.export, "batch_better_fbx_add_mesh")
        assert hasattr(bpy.ops.export, "batch_better_fbx")
        assert addon.ST_Properties.bl_rna.properties["tool"].enum_items.get("KJ_EXPORT")
        print("KJ_EXPORT_REGISTRATION_OK")
    finally:
        addon.unregister()


if __name__ == "__main__":
    run()
