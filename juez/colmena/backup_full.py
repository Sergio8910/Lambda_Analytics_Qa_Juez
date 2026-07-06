"""Backups completos para archivos productivos antes de self-heal."""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class BackedUpFile:
    relative_path: str
    original_checksum: str
    size_bytes: int
    backup_path: str


@dataclass
class FullBackupManifest:
    project_path: str
    backup_dir: str
    created_at: str
    mode: str = "full_file_backup"
    git_commit: str | None = None
    files: list[BackedUpFile] = field(default_factory=list)
    manifest_path: str | None = None


def create_full_backup(
    *,
    project_path: Path | str,
    relative_paths: list[str],
    output_dir: Path | str = "outputs",
) -> FullBackupManifest:
    root = Path(project_path).resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_dir = Path(output_dir) / "backups" / f"colmena_{stamp}"
    original_dir = backup_dir / "original"
    original_dir.mkdir(parents=True, exist_ok=True)

    manifest = FullBackupManifest(
        project_path=str(root),
        backup_dir=str(backup_dir),
        created_at=datetime.now(UTC).isoformat(),
        git_commit=_git_head(root),
    )
    seen: set[str] = set()
    for rel in relative_paths:
        normalized = _normalize_relative(rel)
        if normalized in seen:
            continue
        seen.add(normalized)
        source = (root / normalized).resolve()
        source.relative_to(root)
        if not source.is_file():
            raise FileNotFoundError(f"No existe archivo para backup: {normalized}")
        destination = original_dir / normalized
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        destination.write_bytes(data)
        manifest.files.append(
            BackedUpFile(
                relative_path=normalized,
                original_checksum=_sha256_bytes(data),
                size_bytes=len(data),
                backup_path=str(destination),
            )
        )

    manifest_path = backup_dir / "backup_manifest.json"
    manifest.manifest_path = str(manifest_path)
    manifest_path.write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def load_full_backup_manifest(backup_dir: Path | str) -> FullBackupManifest:
    path = Path(backup_dir)
    manifest_path = path if path.name == "backup_manifest.json" else path / "backup_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = [BackedUpFile(**item) for item in payload.get("files", [])]
    return FullBackupManifest(
        project_path=payload["project_path"],
        backup_dir=payload["backup_dir"],
        created_at=payload["created_at"],
        mode=payload.get("mode", "full_file_backup"),
        git_commit=payload.get("git_commit"),
        files=files,
        manifest_path=str(manifest_path),
    )


def checksum_file(path: Path | str) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _normalize_relative(value: str) -> str:
    rel = value.replace("\\", "/").strip("/")
    if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError(f"Ruta no permitida para backup: {value}")
    return rel


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _git_head(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None
