"""Rigify Export feature integrated into Script Toolkit."""

import sys

import bpy

from . import core, properties, standalone, ui


# Expose every class to Script Toolkit's stale-registration cleanup, including
# the profile panel class whose draw body is reused inside Script Toolkit.
CLASSES = tuple(properties.CLASSES) + tuple(ui.CLASSES)


def _unregister_embedded_exporter():
    package = sys.modules.get(standalone.PACKAGE_NAME)
    if not package or not getattr(package, "standalone", None):
        return
    if not getattr(package.standalone, "IS_EMBEDDED", False):
        return
    package.ui.unregister()
    package.standalone.unregister_save_handler()
    package.properties.unregister()
    for module_name in list(sys.modules):
        if module_name == standalone.PACKAGE_NAME or module_name.startswith(
            standalone.PACKAGE_NAME + "."
        ):
            del sys.modules[module_name]


def draw_ui(layout, context):
    """Draw profile setup in Script Toolkit; Action Export stays in Item."""
    ui.draw_ui(layout, context)


def register():
    _unregister_embedded_exporter()
    properties.register()
    standalone.register_save_handler()
    ui.register(register_panels=False)
    bpy.app.driver_namespace["rex_export_main_addon_active"] = True


def unregister():
    ui.unregister()
    standalone.unregister_save_handler()
    properties.unregister()
    bpy.app.driver_namespace["rex_export_main_addon_active"] = False
