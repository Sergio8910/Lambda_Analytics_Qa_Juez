"""Render y persistencia del reporte del repair loop."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .models import RepairLoopResult


def render_repair_report(result: RepairLoopResult) -> str:
    lines = [
        "=" * 80,
        "  LA COLMENA - PROJECT REPAIR LOOP (DRY-RUN)",
        "  Lambda Analytics - Juez",
        "=" * 80,
        f"  Proyecto           : {result.project_path}",
        f"  Modo reparacion    : {result.config.repair_mode}",
        f"  Casos solicitados  : {result.config.cases_count}",
        f"  Iteraciones max    : {result.config.max_iterations}",
        f"  Score inicial      : {result.initial_score}",
        f"  Score final        : {result.final_score}",
        f"  Readiness final    : {result.readiness_final}",
        f"  Veredicto final    : {result.final_verdict}",
        "  Cambios aplicados  : 0",
        "=" * 80,
        "  NOTA DE SEGURIDAD:",
        "    No se aplicaron cambios porque el modo activo es dry-run/proposal-only.",
        "    El score puede no cambiar entre iteraciones porque esta fase solo genera propuestas.",
        "    Para habilitar mejoras reales se requiere una fase futura con apply-safe.",
        "=" * 80,
        "  RESUMEN POR ITERACION:",
    ]
    for iteration in result.iterations:
        lines.append(
            f"    Iteracion {iteration.iteration}: verdict={iteration.verdict}, "
            f"score={iteration.score_before}->{iteration.score_after}, "
            f"casos={iteration.test_cases_executed}, fallos={iteration.failures_found}, "
            f"fixes={iteration.fixes_proposed}, aplicados={iteration.fixes_applied}, "
            f"bloqueos={iteration.blockers_found}"
        )
        for note in iteration.notes:
            lines.append(f"      - {note}")
    lines.append("")
    lines.append("  CASOS GENERADOS:")
    for case in result.test_cases[:30]:
        lines.append(f"    - {case.id} [{case.case_type}] {case.title} -> {case.target_path or 'proyecto'}")
    lines.append("")
    lines.append("  DIAGNOSTICOS:")
    for diagnosis in result.diagnoses[:40]:
        blocker = " BLOCKER" if diagnosis.has_blocker else ""
        lines.append(f"    - {diagnosis.id} [{diagnosis.severity}] {diagnosis.category}{blocker}: {diagnosis.message}")
        lines.append(f"      causa probable: {diagnosis.probable_cause}")
    lines.append("")
    lines.append("  PROPUESTAS DE REPARACION:")
    for proposal in result.fix_proposals[:40]:
        lines.append(f"    - {proposal.id} [{proposal.severity}] {proposal.fix_type}: {proposal.title}")
        lines.append(f"      target: {proposal.target_path or '(por definir)'}")
        lines.append(f"      applied: {proposal.applied} | reason: {proposal.skipped_reason or 'dry-run'}")
    lines.append("")
    lines.append("  PROXIMOS PASOS:")
    lines.append("    - Revisar propuestas marcadas como manual_review o requires_review.")
    lines.append("    - Priorizar bloqueos criticos antes de pensar en produccion.")
    lines.append("    - Implementar apply-safe solo con diff, rollback y tests verdes.")
    lines.append("=" * 80)
    return "\n".join(lines)


def write_repair_outputs(result: RepairLoopResult, output_dir: Path | str = "outputs") -> RepairLoopResult:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    txt = out / f"colmena_repair_{stamp}.txt"
    js = out / f"colmena_repair_{stamp}.json"
    txt.write_text(render_repair_report(result), encoding="utf-8")
    result = result.model_copy(update={"txt_report_path": str(txt), "json_report_path": str(js)})
    js.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    txt.write_text(render_repair_report(result), encoding="utf-8")
    return result
