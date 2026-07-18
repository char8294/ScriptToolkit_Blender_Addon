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
