from __future__ import annotations

import tempfile
from pathlib import Path

from juez.colmena.backup_full import checksum_file, create_full_backup
from juez.colmena.rollback import restore_all, restore_one


def test_full_backup_and_restore_all_validates_checksum() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = base / "project"
        project.mkdir()
        target = project / "app.py"
        target.write_text("ORIGINAL = True\n", encoding="utf-8")
        original_checksum = checksum_file(target)

        backup = create_full_backup(project_path=project, relative_paths=["app.py"], output_dir=base / "outputs")
        target.write_text("ORIGINAL = False\n", encoding="utf-8")

        result = restore_all(backup.backup_dir, reason="test rollback")

        assert result.restored_items == 1
        assert result.failed_items == 0
        assert target.read_text(encoding="utf-8") == "ORIGINAL = True\n"
        assert checksum_file(target) == original_checksum
        assert result.audit_log_path and Path(result.audit_log_path).exists()


def test_restore_one_restores_only_requested_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        project = base / "project"
        project.mkdir()
        one = project / "one.txt"
        two = project / "two.txt"
        one.write_text("one\n", encoding="utf-8")
        two.write_text("two\n", encoding="utf-8")

        backup = create_full_backup(project_path=project, relative_paths=["one.txt", "two.txt"], output_dir=base / "outputs")
        one.write_text("changed-one\n", encoding="utf-8")
        two.write_text("changed-two\n", encoding="utf-8")

        result = restore_one(backup.backup_dir, "one.txt", reason="partial")

        assert result.restored_items == 1
        assert one.read_text(encoding="utf-8") == "one\n"
        assert two.read_text(encoding="utf-8") == "changed-two\n"
