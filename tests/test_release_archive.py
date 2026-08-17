from pathlib import Path
from zipfile import ZipFile

import update_utils


def test_release_archive_includes_feature_modules(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    repo_root = Path(__file__).resolve().parents[1]

    for relative_path in (
        "__init__.py",
        "update_utils.py",
        "worker_entry.py",
        "worker_jobs.py",
    ):
        target = source_dir / relative_path
        target.write_bytes((repo_root / relative_path).read_bytes())

    feature_dir = source_dir / "features"
    feature_dir.mkdir()
    for relative_path in (
        "features/__init__.py",
        "features/biped_names.py",
        "features/hair_check.py",
        "features/empty_to_bone.py",
        "features/align_bones.py",
        "features/arp_retarget_preset.py",
        "features/kj_export.py",
        "features/root_motion.py",
        "features/turntable_camera.py",
        "features/quick_render.py",
        "features/learn_node_blender.py",
        "features/learn_node_data/curve_nodes.json",
        "features/learn_node_data/geometry_nodes.json",
        "features/learn_node_data/input_nodes.json",
        "features/learn_node_data/math_nodes.json",
        "features/learn_node_data/mesh_nodes.json",
        "features/learn_node_data/misc_nodes.json",
    ):
        target = source_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repo_root / relative_path).read_bytes())

    archive_path = tmp_path / "release.zip"
    update_utils.build_release_archive(source_dir, archive_path, "script_toolkit")

    with ZipFile(archive_path) as archive:
        names = set(archive.namelist())

    assert "script_toolkit/features/__init__.py" in names
    assert "script_toolkit/features/kj_export.py" in names
    assert "script_toolkit/features/root_motion.py" in names
    assert "script_toolkit/features/turntable_camera.py" in names
    assert "script_toolkit/features/quick_render.py" in names
    assert "script_toolkit/features/learn_node_blender.py" in names
    assert "script_toolkit/features/learn_node_data/misc_nodes.json" in names
