"""Reportes para self-heal autonomo de La Colmena."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .self_heal_models import SelfHealResult


def render_self_heal_report(result: SelfHealResult) -> str:
    lines = [
        "=" * 80,
        "  LA COLMENA - SELF-HEAL AUTONOMO",
        "  Lambda Analytics - Juez",
        "=" * 80,
        f"  Proyecto                : {result.project_path}",
        f"  Min confidence          : {result.min_confidence}",
        f"  Max iterations          : {result.max_iterations}",
        f"  Max lines per fix       : {result.max_lines_per_fix}",
        f"  Fast reeval             : {str(result.fast_reeval).lower()}",
        f"  Score inicial           : {result.score_initial}",
        f"  Score final             : {result.score_final}",
        f"  Readiness inicial       : {result.readiness_initial}",
        f"  Readiness final         : {result.readiness_final}",
        f"  Fixes mantenidos        : {result.kept_fixes}",
        f"  Fixes revertidos        : {result.rolled_back_fixes}",
        f"  Hallazgos bloqueados    : {result.blocked_findings}",
        f"  Fixes fallidos          : {result.failed_fixes}",
        f"  Fixer generico costo    : {_format_cost_summary(result.generic_fixer_cost_summary)}",
        f"  Audit log               : {result.audit_log_path or 'n/a'}",
        "=" * 80,
        "  ITERACIONES:",
    ]
    if not result.iterations:
        lines.append("    (sin iteraciones)")
    for item in result.iterations:
        lines.append(
            f"    - {item.iteration}. {item.finding_id} [{item.severity}] {item.finding_title}"
        )
        lines.append(f"      archivo    : {item.target_path or '(sin archivo)'}")
        lines.append(f"      fix        : {item.fix_type or 'n/a'} | confianza {item.confidence:.2f}")
        lines.append(f"      decision   : {item.decision}")
        lines.append(f"      razon      : {item.reason}")
        lines.append(f"      score      : {item.score_before} -> {item.score_after}")
        if item.backup_dir:
            lines.append(f"      backup     : {item.backup_dir}")
        if item.rollback_audit_path:
            lines.append(f"      rollback   : {item.rollback_audit_path}")
        if item.generic_fixer_attempts:
            lines.append("      fixer generico - intentos:")
            for att in item.generic_fixer_attempts:
                estado = "APROBADO" if att.get("approved") else "descartado"
                usage = _format_attempt_usage(att)
                lines.append(f"        intento {att.get('attempt_no')} [{estado}]{usage}: {att.get('reason')}")
    lines.append("")
    lines.append("  REQUIERE REVISION HUMANA:")
    if not result.human_review_required:
        lines.append("    (ninguno)")
    for item in result.human_review_required:
        lines.append(
            f"    - {item.get('id')} [{item.get('severity')}] {item.get('title')} "
            f"({item.get('file') or 'sin archivo'})"
        )
        lines.append(f"      razon: {item.get('reason')}")
        if item.get("generic_fixer_attempts"):
            lines.append("      fixer generico - propuestas descartadas:")
            for att in item["generic_fixer_attempts"]:
                usage = _format_attempt_usage(att)
                lines.append(f"        intento {att.get('attempt_no')}{usage}: {att.get('reason')}")
    lines.extend(
        [
            "=" * 80,
            "  POLITICA:",
            "    Todo archivo modificado tuvo backup completo previo.",
            "    Si el gate de salida falla, se ejecuta rollback automatico.",
            "    Archivos sensibles o de blocklist quedan para revision humana.",
            "=" * 80,
        ]
    )
    return "\n".join(lines)


def _format_cost_summary(summary: dict | None) -> str:
    if not summary:
        return "n/a"
    return (
        f"{summary.get('total_calls', 0)} llamada(s), "
        f"{summary.get('total_tokens', 0)} tokens, "
        f"USD ~{summary.get('total_cost_usd', 0)}"
    )


def _format_attempt_usage(att: dict) -> str:
    model = att.get("model")
    total_tokens = att.get("total_tokens")
    cost = att.get("estimated_cost_usd")
    if not model and total_tokens is None and cost is None:
        return ""
    parts = []
    if model:
        parts.append(str(model))
    if total_tokens is not None:
        parts.append(f"{total_tokens} tokens")
    if cost is not None:
        parts.append(f"USD ~{cost}")
    return " (" + ", ".join(parts) + ")"


def write_self_heal_report(result: SelfHealResult, output_dir: Path | str = "outputs") -> SelfHealResult:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    txt = out / f"colmena_selfheal_{stamp}.txt"
    js = out / f"colmena_selfheal_{stamp}.json"
    result.txt_report_path = str(txt)
    result.json_report_path = str(js)
    txt.write_text(render_self_heal_report(result), encoding="utf-8")
    js.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
