"""Rollback seguro desde backups completos de La Colmena."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .backup_full import checksum_file, load_full_backup_manifest


@dataclass
class RollbackItemResult:
    relative_path: str
    restored: bool
    checksum_valid: bool
    message: str


@dataclass
class RollbackResult:
    backup_dir: str
    reason: str
    executed_at: str
    restored_items: int = 0
    failed_items: int = 0
    results: list[RollbackItemResult] = field(default_factory=list)
    audit_log_path: str | None = None


def restore_all(backup_dir: Path | str, *, reason: str = "rollback solicitado") -> RollbackResult:
    manifest = load_full_backup_manifest(backup_dir)
    result = RollbackResult(
        backup_dir=manifest.backup_dir,
        reason=reason,
        executed_at=datetime.now(UTC).isoformat(),
    )
    for item in manifest.files:
        result.results.append(_restore_item(manifest.project_path, item.relative_path, item.backup_path, item.original_checksum))
    _summarize(result)
    return _write_audit(result)


def restore_one(
    backup_dir: Path | str,
    relative_path: str,
    *,
    reason: str = "rollback parcial solicitado",
) -> RollbackResult:
    manifest = load_full_backup_manifest(backup_dir)
    result = RollbackResult(
        backup_dir=manifest.backup_dir,
        reason=reason,
        executed_at=datetime.now(UTC).isoformat(),
    )
    matches = [item for item in manifest.files if item.relative_path == relative_path.replace("\\", "/").strip("/")]
    if not matches:
        result.results.append(
            RollbackItemResult(
                relative_path=relative_path,
                restored=False,
                checksum_valid=False,
                message="Archivo no existe en el backup manifest.",
            )
        )
    else:
        item = matches[0]
        result.results.append(_restore_item(manifest.project_path, item.relative_path, item.backup_path, item.original_checksum))
    _summarize(result)
    return _write_audit(result)


def _restore_item(project_path: str, relative_path: str, backup_path: str, expected_checksum: str) -> RollbackItemResult:
    root = Path(project_path).resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
        data = Path(backup_path).read_bytes()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        valid = checksum_file(target) == expected_checksum
        return RollbackItemResult(
            relative_path=relative_path,
            restored=valid,
            checksum_valid=valid,
            message="Restaurado y checksum validado." if valid else "Restaurado pero checksum no coincide.",
        )
    except Exception as exc:
        return RollbackItemResult(
            relative_path=relative_path,
            restored=False,
            checksum_valid=False,
            message=f"Rollback fallo: {type(exc).__name__}: {exc}",
        )


def _summarize(result: RollbackResult) -> None:
    result.restored_items = sum(1 for item in result.results if item.restored and item.checksum_valid)
    result.failed_items = len(result.results) - result.restored_items


def _write_audit(result: RollbackResult) -> RollbackResult:
    backup_dir = Path(result.backup_dir)
    path = backup_dir / f"rollback_audit_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result.audit_log_path = str(path)
    path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
