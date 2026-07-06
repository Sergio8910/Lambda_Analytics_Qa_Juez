"""Generacion de manifiestos de aprobacion humana para patches."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .approval_models import PatchApprovalItem, PatchApprovalManifest, PatchExportResult


def build_approval_manifest(export_result: PatchExportResult) -> PatchApprovalManifest:
    items = [
        PatchApprovalItem(
            proposal_id=item.proposal_id,
            patch_id=item.patch_id,
            target_path=item.target_path,
            patch_file_path=item.patch_file_path,
            status="pending",
            decision=None,
            risk=item.risk,
            safe_to_apply=item.safe_to_apply,
            checksum=item.checksum,
        )
        for item in export_result.items
        if item.status == "exported"
    ]
    return _summarize(
        PatchApprovalManifest(
            project_path=export_result.project_path,
            generated_at=datetime.now(UTC).isoformat(),
            items=items,
        )
    )


def write_approval_manifest(
    manifest: PatchApprovalManifest,
    output_dir: Path | str = "outputs",
) -> PatchApprovalManifest:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    path = out / f"colmena_patch_approval_{stamp}.json"
    manifest.manifest_path = str(path)
    path.write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _summarize(manifest: PatchApprovalManifest) -> PatchApprovalManifest:
    manifest.total_items = len(manifest.items)
    manifest.pending_items = sum(1 for item in manifest.items if item.status == "pending")
    manifest.approved_items = sum(1 for item in manifest.items if item.status == "approved")
    manifest.rejected_items = sum(1 for item in manifest.items if item.status == "rejected")
    manifest.blocked_items = sum(1 for item in manifest.items if item.status == "blocked")
    manifest.files_modified = 0
    manifest.fixes_applied = 0
    return manifest
