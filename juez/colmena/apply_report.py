"""Reporte TXT/JSON para aplicacion segura de patches aprobados."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .patch_apply_models import PatchApplyResult


def render_patch_apply_report(result: PatchApplyResult) -> str:
    lines = [
        "=" * 80,
        "  LA COLMENA - APPLY APPROVED PATCHES",
        "  Lambda Analytics - Juez",
        "=" * 80,
        f"  Proyecto evaluado          : {result.project_path}",
        f"  Approval file              : {result.approval_file_path}",
        f"  Apply approved patches     : {str(result.apply_approved_patches).lower()}",
        f"  Approval file valid        : {str(result.approval_valid).lower()}",
        f"  Approved patches           : {result.approved_items}",
        f"  Rejected patches           : {result.rejected_items}",
        f"  Pending patches            : {result.pending_items}",
        f"  Applicable patches         : {result.applicable_items}",
        f"  Applied patches            : {result.applied_items}",
        f"  Blocked patches            : {result.blocked_items}",
        f"  Skipped patches            : {result.skipped_items}",
        f"  Failed patches             : {result.failed_items}",
        f"  Files created              : {result.files_created}",
        f"  Files modified             : {result.files_modified}",
        f"  Files overwritten          : {result.files_overwritten}",
        f"  Backup created             : {str(bool(result.backup_path)).lower()}",
        f"  Backup path                : {result.backup_path or 'n/a'}",
        f"  Post-evaluation executed   : {str(result.post_evaluation_executed).lower()}",
        f"  Score before               : {result.score_before}",
        f"  Score after                : {result.score_after}",
        f"  Readiness before           : {result.readiness_before}",
        f"  Readiness after            : {result.readiness_after}",
        f"  Rollback status            : {result.rollback_status}",
        f"  Rollback reason            : {result.rollback_reason or 'n/a'}",
        f"  Audit log path             : {result.audit_log_path or 'n/a'}",
        "=" * 80,
        "  RESULTADOS POR PATCH:",
    ]
    if result.item_results:
        for item in result.item_results:
            lines.append(
                f"    - {item.patch_id} [{item.status}] {item.target_path or '(sin target)'}"
            )
            lines.append(f"      {item.error or item.skipped_reason or item.message}")
    else:
        lines.append("    (sin patches procesados)")
    if result.warnings:
        lines.append("")
        lines.append("  ADVERTENCIAS:")
        lines.extend(f"    - {warning}" for warning in result.warnings)
    lines.extend(
        [
            "=" * 80,
            "  POLITICA DE SEGURIDAD:",
            "    Esta fase solo crea archivos nuevos aprobados y permitidos.",
            "    No modifica, sobrescribe, mueve, borra ni despliega nada.",
            "=" * 80,
        ]
    )
    return "\n".join(lines)


def write_patch_apply_report(
    result: PatchApplyResult,
    output_dir: Path | str = "outputs",
) -> PatchApplyResult:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    txt = out / f"colmena_patch_apply_{stamp}.txt"
    js = out / f"colmena_patch_apply_{stamp}.json"
    result.txt_report_path = str(txt)
    result.json_report_path = str(js)
    txt.write_text(render_patch_apply_report(result), encoding="utf-8")
    js.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
