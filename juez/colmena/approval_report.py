"""Reporte de exportacion y aprobacion de patches de La Colmena."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .approval_models import (
    PatchApprovalManifest,
    PatchApprovalReport,
    PatchApprovalValidationResult,
    PatchExportResult,
)
from .patch_models import PatchPlan


def build_patch_approval_report(
    *,
    patch_plan: PatchPlan | None,
    export_result: PatchExportResult | None,
    manifest: PatchApprovalManifest | None,
    validation: PatchApprovalValidationResult | None = None,
    approval_file: Path | str | None = None,
    export_patches: bool = False,
) -> PatchApprovalReport:
    project_path = patch_plan.project_path if patch_plan else manifest.project_path if manifest else ""
    report = PatchApprovalReport(
        project_path=project_path,
        repair_mode=patch_plan.mode if patch_plan else "dry-run",
        generate_diffs=bool(patch_plan),
        export_patches=export_patches,
        total_patch_items=patch_plan.total_items if patch_plan else 0,
        patches_exported=export_result.exported_items if export_result else 0,
        patches_blocked=export_result.blocked_items if export_result else 0,
        patches_skipped=export_result.skipped_items if export_result else 0,
        pending_patches=manifest.pending_items if manifest else 0,
        manifest_generated=manifest is not None and manifest.manifest_path is not None,
        manifest_path=manifest.manifest_path if manifest else None,
        approval_file_provided=approval_file is not None,
        approval_file_path=str(approval_file) if approval_file else None,
        exported_files=[
            item.patch_file_path
            for item in export_result.items
            if item.status == "exported" and item.patch_file_path
        ]
        if export_result
        else [],
    )
    if validation:
        report.approval_file_valid = validation.valid
        report.approved_patches = validation.approved_items
        report.rejected_patches = validation.rejected_items
        report.pending_patches = validation.pending_items
        report.blocked_patches = validation.blocked_items
        report.validation_errors = validation.errors
        report.validation_warnings = validation.warnings
    return report


def render_patch_approval_report(report: PatchApprovalReport) -> str:
    lines = [
        "=" * 80,
        "  LA COLMENA - PATCH EXPORT & APPROVAL GATE",
        "  Lambda Analytics - Juez",
        "=" * 80,
        f"  Proyecto evaluado           : {report.project_path}",
        f"  Repair mode                 : {report.repair_mode}",
        f"  Generate diffs              : {str(report.generate_diffs).lower()}",
        f"  Export patches              : {str(report.export_patches).lower()}",
        f"  Total patch items           : {report.total_patch_items}",
        f"  Patch files generated       : {report.patches_exported}",
        f"  Patches blocked             : {report.patches_blocked}",
        f"  Patches skipped             : {report.patches_skipped}",
        f"  Approval manifest generated : {str(report.manifest_generated).lower()}",
        f"  Approval file provided      : {str(report.approval_file_provided).lower()}",
        f"  Approval file validated     : {str(report.approval_file_valid).lower() if report.approval_file_valid is not None else 'n/a'}",
        f"  Approved patches            : {report.approved_patches}",
        f"  Rejected patches            : {report.rejected_patches}",
        f"  Pending patches             : {report.pending_patches}",
        f"  Blocked patches             : {report.blocked_patches}",
        f"  Files modified              : {report.files_modified}",
        f"  Fixes applied               : {report.fixes_applied}",
        "=" * 80,
    ]
    if report.manifest_path:
        lines.append(f"  Manifest: {report.manifest_path}")
    if report.approval_file_path:
        lines.append(f"  Approval file: {report.approval_file_path}")
    if report.exported_files:
        lines.append("  PATCH FILES:")
        lines.extend(f"    - {path}" for path in report.exported_files)
    if report.validation_errors:
        lines.append("  ERRORS:")
        lines.extend(f"    - {error}" for error in report.validation_errors)
    if report.validation_warnings:
        lines.append("  WARNINGS:")
        lines.extend(f"    - {warning}" for warning in report.validation_warnings)
    lines.extend(
        [
            "=" * 80,
            "  ADVERTENCIA:",
            "    Este reporte no aplica patches. Aprobar un patch no modifica archivos.",
            "    La aplicacion real queda reservada para una fase futura de apply-safe.",
            "=" * 80,
        ]
    )
    return "\n".join(lines)


def write_patch_approval_report(
    report: PatchApprovalReport,
    output_dir: Path | str = "outputs",
) -> PatchApprovalReport:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    txt = out / f"colmena_patch_approval_report_{stamp}.txt"
    js = out / f"colmena_patch_approval_report_{stamp}.json"
    report.txt_report_path = str(txt)
    report.json_report_path = str(js)
    txt.write_text(render_patch_approval_report(report), encoding="utf-8")
    js.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
