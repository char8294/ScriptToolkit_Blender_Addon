"""Blender background test for the restricted context used by addon_utils."""

import importlib.util
import sys
from pathlib import Path

import bpy
from _bpy_restrict_state import RestrictBlend


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
    with RestrictBlend():
        addon.register()
    addon.unregister()
    print("RESTRICTED_REGISTER_OK")


if __name__ == "__main__":
    run()
