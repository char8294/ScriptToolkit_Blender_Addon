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
    assert addon.bl_info["version"] == (0, 6, 21)
    assert addon.arp_retarget_preset._refresh_all_preset_items(SimpleNamespace()) == 0

    # Reproduce the stale-class state caused by an older failed unregister.
    addon.biped_names.register()
    def failed_unregister():
        raise RuntimeError("simulated old unregister failure")

    addon.unregister = failed_unregister
    addon = reload_addon(addon)

    addon.register()
    assert hasattr(bpy.types.Scene, "turntable_props")
    assert hasattr(bpy.types.Scene, "qvr_props")
    assert hasattr(bpy.types.Object, "rex_profiles")
    tool_items = addon.ST_Properties.bl_rna.properties["tool"].enum_items_static
    assert any(item.identifier == "RIGIFY_EXPORT" for item in tool_items)
    assert addon.rigify_export.ui._registered_class(addon.rigify_export.ui.REX_PT_profiles) is None
    assert addon.rigify_export.ui._registered_class(addon.rigify_export.ui.REX_PT_export)
    assert addon.rigify_export.ui.REX_PT_export.bl_category == "Item"
    assert addon.learn_node_blender.get_node_data()
    bpy.context.scene.script_toolkit.tool = "RIGIFY_EXPORT"
    addon.ST_PT_panel.draw(SimpleNamespace(layout=_RecordingLayout()), bpy.context)
    addon.register()
    addon.unregister()
    addon.register()
    addon = reload_addon(addon)
    addon.register()
    addon.unregister()

    # The exporter can still register its legacy panels from an embedded
    # .blend-file script when Script Toolkit is not enabled.
    addon.rigify_export.properties.register()
    addon.rigify_export.ui.register(register_panels=True)
    assert addon.rigify_export.ui._registered_class(addon.rigify_export.ui.REX_PT_profiles)
    assert addon.rigify_export.ui._registered_class(addon.rigify_export.ui.REX_PT_export)
    addon.rigify_export.ui.unregister()
    addon.rigify_export.properties.unregister()
    print("ADDON_REPEAT_REGISTER_OK")


class _RecordingLayout:
    """Minimal layout surface for drawing the feature's empty state."""

    def box(self):
        return self

    def row(self, **_kwargs):
        return self

    def column(self, **_kwargs):
        return self

    def label(self, **_kwargs):
        return None

    def prop(self, *_args, **_kwargs):
        return None

    def operator(self, *_args, **_kwargs):
        return SimpleNamespace()

    def separator(self):
        return None


if __name__ == "__main__":
    run()
