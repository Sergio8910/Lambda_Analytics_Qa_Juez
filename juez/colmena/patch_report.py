"""Reporte TXT/JSON para planes de patch de La Colmena."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .patch_models import PatchPlan


def render_patch_plan_report(plan: PatchPlan) -> str:
    lines = [
        "=" * 80,
        "  LA COLMENA - SAFE FIX DIFF PLANNER",
        "  Lambda Analytics - Juez",
        "=" * 80,
        f"  Proyecto evaluado : {plan.project_path}",
        f"  Repair mode       : {plan.mode}",
        f"  Generate diffs    : {str(plan.generate_diffs).lower()}",
        f"  Patch generated   : {bool(plan.items)}",
        f"  Patch items       : {plan.total_items}",
        f"  Safe patch items  : {plan.safe_items}",
        f"  Blocked items     : {plan.blocked_items}",
        f"  Review items      : {plan.review_items}",
        f"  Files modified    : {plan.files_modified}",
        f"  Fixes applied     : {plan.fixes_applied}",
        "=" * 80,
        "  ADVERTENCIA:",
        "    Este reporte es un preview. No se modifico el proyecto evaluado.",
        "    apply-safe todavia no esta habilitado para aplicar cambios reales.",
        "=" * 80,
    ]
    if not plan.items:
        lines.append("  No se generaron propuestas de reparacion elegibles para diffs.")
        lines.append("=" * 80)
        return "\n".join(lines)

    for heading, predicate in [
        ("PATCHES SEGUROS", lambda item: item.status == "planned" and item.safe_to_apply),
        ("PATCHES QUE REQUIEREN REVISION", lambda item: item.status == "requires_review" or item.requires_review),
        ("PATCHES BLOQUEADOS", lambda item: item.status == "blocked"),
    ]:
        lines.append(f"  {heading}:")
        selected = [item for item in plan.items if predicate(item)]
        if not selected:
            lines.append("    (ninguno)")
        for item in selected:
            lines.append(f"    - {item.proposal_id} [{item.risk}] {item.action}: {item.target_path or '(sin archivo)'}")
            lines.append(f"      status: {item.status} | source: {item.source}")
            lines.append(f"      razon : {item.blocked_reason or item.reason}")
        lines.append("")

    lines.append("  DIFF PREVIEWS:")
    for item in plan.items:
        if item.diff_preview:
            lines.append(f"--- Diff para {item.target_path} ({item.proposal_id}) ---")
            lines.append(item.diff_preview.rstrip())
        elif item.status == "blocked":
            lines.append(f"--- {item.proposal_id}: diff no generado ({item.blocked_reason}) ---")
    lines.append("=" * 80)
    return "\n".join(lines)


def write_patch_plan_outputs(plan: PatchPlan, output_dir: Path | str = "outputs") -> PatchPlan:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    txt = out / f"colmena_patch_plan_{stamp}.txt"
    js = out / f"colmena_patch_plan_{stamp}.json"
    plan = plan.model_copy(update={"txt_report_path": str(txt), "json_report_path": str(js)})
    txt.write_text(render_patch_plan_report(plan), encoding="utf-8")
    js.write_text(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan
