"""Backups livianos para aplicacion create_file_only."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def create_apply_backup_manifest(
    *,
    project_path: Path | str,
    target_paths: list[str],
    output_dir: Path | str = "outputs",
) -> Path:
    out = Path(output_dir) / "backups" / f"colmena_apply_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    root = Path(project_path).resolve()
    payload = {
        "project_path": str(root),
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "create_file_only",
        "existing_paths_checked": target_paths,
        "existing_paths": [target for target in target_paths if (root / target).exists()],
    }
    path = out / "backup_manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
