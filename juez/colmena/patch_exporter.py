"""Exporta diffs preview de La Colmena como archivos .patch."""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from .approval_models import ExportedPatchFile, PatchExportResult
from .patch_models import PatchPlanItem
from .safety_gates import can_export_patch


def export_patch_plan_items(
    items: list[PatchPlanItem],
    *,
    project_path: Path | str,
    output_dir: Path | str = "outputs",
) -> PatchExportResult:
    generated_at = datetime.now(UTC).isoformat()
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out = Path(output_dir)
    patches_dir = out / "patches"
    patches_dir.mkdir(parents=True, exist_ok=True)

    exported: list[ExportedPatchFile] = []
    for item in items:
        patch_id = _patch_id(item.proposal_id)
        allowed, reason = can_export_patch(item)
        if not allowed:
            exported.append(
                ExportedPatchFile(
                    proposal_id=item.proposal_id,
                    patch_id=patch_id,
                    target_path=item.target_path,
                    patch_file_path=None,
                    status="blocked" if item.status == "blocked" else "skipped",
                    reason=reason or "Patch no elegible para exportacion.",
                    risk=item.risk,
                    safe_to_apply=item.safe_to_apply,
                )
            )
            continue

        content = item.diff_preview or ""
        filename = f"colmena_{stamp}_{_safe_name(item.proposal_id)}.patch"
        patch_path = patches_dir / filename
        patch_path.write_text(content, encoding="utf-8")
        exported.append(
            ExportedPatchFile(
                proposal_id=item.proposal_id,
                patch_id=patch_id,
                target_path=item.target_path,
                patch_file_path=str(patch_path),
                status="exported",
                reason="Patch exportado para aprobacion humana. No fue aplicado.",
                risk=item.risk,
                safe_to_apply=item.safe_to_apply,
                checksum=_sha256_file(patch_path),
            )
        )

    return _summarize(
        PatchExportResult(
            project_path=str(Path(project_path).resolve()),
            generated_at=generated_at,
            output_dir=str(out),
            patches_dir=str(patches_dir),
            items=exported,
        )
    )


def _summarize(result: PatchExportResult) -> PatchExportResult:
    result.total_items = len(result.items)
    result.exported_items = sum(1 for item in result.items if item.status == "exported")
    result.skipped_items = sum(1 for item in result.items if item.status == "skipped")
    result.blocked_items = sum(1 for item in result.items if item.status == "blocked")
    result.files_modified = 0
    result.fixes_applied = 0
    return result


def _patch_id(proposal_id: str) -> str:
    return f"patch_{_safe_name(proposal_id)}"


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe.strip("._") or "patch"


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
