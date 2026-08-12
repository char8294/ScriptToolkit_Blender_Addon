"""Blender background test for repeated add-on registration."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


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


def reload_addon(addon):
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "script_toolkit",
        repo_root / "__init__.py",
        submodule_search_locations=[str(repo_root)],
    )
    spec.loader.exec_module(addon)
    return addon


def run():
    addon = load_addon()
    assert addon.bl_info["version"] == (0, 4, 9)
    assert addon.arp_retarget_preset._refresh_all_preset_items(SimpleNamespace()) == 0
    addon.register()
    addon.register()
    addon.unregister()
    addon.register()
    addon = reload_addon(addon)
    addon.register()
    addon.unregister()
    print("ADDON_REPEAT_REGISTER_OK")


if __name__ == "__main__":
    run()
