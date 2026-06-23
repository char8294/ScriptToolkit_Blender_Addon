# Script Toolkit

Blender 5.1+ Add-on that collects the FBX scripts in this repository.

## Install

1. Zip the `script_toolkit` directory, keeping `script_toolkit/__init__.py` at the top level inside the zip.
2. In Blender, open **Edit > Preferences > Add-ons** and use **Install from Disk** to choose that zip file.
3. Enable **Import-Export: Script Toolkit**.
4. Open **3D View > N-panel > Script Toolkit**.

## Execution model

- **Batch Tools** run in a separate Blender process started with `--factory-startup`. They never clear or replace the scene in the Blender instance used to start the job.
- **Delete Faces by Material** and **Clear Custom Properties** operate immediately on the current selection and support Undo.
- **Check Hair And Cap** and **Biped Names Helper** are also selected from the Script Toolkit dropdown; their original controls are shown in the same panel instead of separate Sidebar tabs.

Batch progress/result JSON files are stored temporarily in `%TEMP%/script_toolkit_jobs`.

## Batch tools

- Re-export FBX
- Skin Volume Weight
- Material ID Cleanup
- Separate by Material

## Current-scene tools

- Delete Faces by Material
- Clear Custom Properties
- Check Hair And Cap
- Biped Names Helper

Set different Input and Output folders. The panel validates the paths and checks for FBX files before starting a worker.
