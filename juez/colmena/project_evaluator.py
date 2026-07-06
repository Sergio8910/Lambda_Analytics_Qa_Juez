"""Evaluador de proyectos completos para La Colmena."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .classifier import classify_project
from .colmena import Componente, run_colmena
from .models import NormalizedFinding, ProjectEvaluationReport, ProjectScore
from .scanner import scan_project
from .workers import evaluate_project_workers

_CATEGORY_WEIGHTS = {
    "security": 30.0,
    "api": 10.0,
    "workflow": 10.0,
    "architecture": 15.0,
    "agent": 7.5,
    "prompt": 7.5,
    "integration": 10.0,
    "documentation": 5.0,
    "deployment": 5.0,
    "testing": 5.0,
    "performance": 5.0,
    "maintainability": 10.0,
}
_SEVERITY_IMPACT = {"critical": 1.0, "high": 0.45, "medium": 0.18, "low": 0.06, "info": 0.0}


def evaluate_project_path(
    project_path: Path | str,
    *,
    project_id: str | None = None,
    incluir_dinamicas: bool = False,
) -> ProjectEvaluationReport:
    root = Path(project_path).resolve()
    inventory = scan_project(root)
    classification = classify_project(inventory)
    findings = evaluate_project_workers(root, inventory)

    legacy_score: float | None = None
    legacy_findings: list[dict[str, Any]] = []
    legacy_components = _components_from_inventory(root, inventory)
    if legacy_components:
        legacy_result = run_colmena(
            project_id or root.name,
            legacy_components,
            incluir_dinamicas=incluir_dinamicas,
        )
        legacy_score = legacy_result.score
        legacy_findings = legacy_result.hallazgos
        findings.extend(_normalize_legacy_findings(legacy_result.hallazgos, len(findings)))

    score = score_project(findings)
    return ProjectEvaluationReport(
        project_id=project_id or root.name,
        root_path=str(root),
        inventory=inventory,
        classification=classification,
        findings=sorted(findings, key=lambda f: (_severity_rank(f.severity), f.category, f.id)),
        score=score,
        recommendations=_recommendations(findings),
        auto_fixable=[f.id for f in findings if f.auto_fix_available],
        human_review_required=[f.id for f in findings if not f.auto_fix_available and f.severity in {"critical", "high"}],
        legacy_component_score=legacy_score,
        legacy_component_findings=legacy_findings,
    )


def score_project(findings: list[NormalizedFinding]) -> ProjectScore:
    by_severity = Counter(f.severity for f in findings)
    by_category = Counter(f.category for f in findings)
    penalties_by_category: dict[str, float] = {}
    for finding in findings:
        weight = _CATEGORY_WEIGHTS.get(finding.category, 5.0)
        penalties_by_category[finding.category] = penalties_by_category.get(finding.category, 0.0) + (
            weight * _SEVERITY_IMPACT[finding.severity]
        )
    weighted_penalty = min(100.0, sum(min(_CATEGORY_WEIGHTS.get(cat, 5.0), penalty) for cat, penalty in penalties_by_category.items()))
    score = round(max(0.0, 100.0 - weighted_penalty), 1)
    critical_security = any(f.severity == "critical" and f.category == "security" for f in findings)
    critical_count = by_severity.get("critical", 0)
    high_count = by_severity.get("high", 0)
    if critical_security:
        status = "blocked_by_critical_findings"
    elif critical_count:
        status = "not_ready_for_production"
    elif score >= 85.0 and high_count == 0:
        status = "ready_for_production"
    elif score >= 70.0:
        status = "ready_with_observations"
    else:
        status = "not_ready_for_production"
    return ProjectScore(
        score=score,
        status=status,  # type: ignore[arg-type]
        blocking_findings=critical_count + high_count,
        critical_findings=critical_count,
        high_findings=high_count,
        medium_findings=by_severity.get("medium", 0),
        by_category=dict(sorted(by_category.items())),
        by_severity=dict(sorted(by_severity.items())),
        weighted_penalty=round(weighted_penalty, 1),
    )


def render_project_report(report: ProjectEvaluationReport) -> str:
    lines = [
        "=" * 80,
        "  LA COLMENA - EVALUACION INTEGRAL DE PROYECTO IA",
        "  Lambda Analytics - Juez",
        "=" * 80,
        f"  Proyecto      : {report.project_id}",
        f"  Ruta          : {report.root_path}",
        f"  Tipo detectado: {report.classification.project_type} ({report.classification.confidence:.2f})",
        f"  Score         : {report.score.score}/100",
        f"  Readiness     : {report.score.status}",
        f"  Hallazgos     : {_summary(report.score.by_severity)}",
        "=" * 80,
        "  INVENTARIO TECNICO:",
    ]
    for key, value in sorted(report.inventory.detected_assets.items()):
        if value:
            lines.append(f"    - {key}: {value}")
    if report.inventory.frameworks:
        lines.append(f"    - frameworks: {', '.join(report.inventory.frameworks)}")
    if report.inventory.integrations:
        lines.append(f"    - integraciones: {', '.join(report.inventory.integrations)}")
    lines.append("")
    lines.append("  POR QUE SE CLASIFICO ASI:")
    for reason in report.classification.reasons:
        lines.append(f"    - {reason}")
    lines.append("")
    lines.append("  HALLAZGOS PRIORITARIOS:")
    if report.findings:
        for finding in report.findings[:40]:
            where = f" ({finding.file}:{finding.line})" if finding.file and finding.line else (f" ({finding.file})" if finding.file else "")
            lines.append(f"    [{finding.severity.upper()}] {finding.id} {finding.category}: {finding.title}{where}")
            lines.append(f"      {finding.description}")
            if finding.recommendation:
                lines.append(f"      Recomendacion: {finding.recommendation}")
    else:
        lines.append("    Sin hallazgos relevantes.")
    lines.append("")
    lines.append("  AUTO-FIX:")
    lines.append(f"    Corregibles automaticamente: {', '.join(report.auto_fixable) or 'ninguno'}")
    lines.append(f"    Requieren revision humana : {', '.join(report.human_review_required) or 'ninguno'}")
    lines.append("")
    lines.append("  RECOMENDACIONES PRIORIZADAS:")
    for rec in report.recommendations:
        lines.append(f"    - {rec}")
    lines.append("=" * 80)
    return "\n".join(lines)


def write_project_outputs(report: ProjectEvaluationReport, output_dir: Path | str = "outputs") -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = _safe_name(report.project_id)
    txt = out / f"colmena_project_{base}.txt"
    js = out / f"colmena_project_{base}.json"
    txt.write_text(render_project_report(report), encoding="utf-8")
    js.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return txt, js


def _components_from_inventory(root: Path, inventory) -> list[Componente]:
    components: list[Componente] = []
    for asset in inventory.assets:
        path = root / asset.path
        if asset.kind == "n8n_workflow":
            try:
                workflow = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            components.append(
                Componente(
                    kind="n8n",
                    nombre=asset.name or path.stem,
                    workflow_json=workflow,
                    workflow_path=str(path),
                )
            )
        elif asset.kind in {"prompt", "prompt_doc"}:
            try:
                prompt = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            components.append(
                Componente(kind="prompt", nombre=asset.name or path.stem, prompt=prompt, prompt_path=str(path))
            )
    return components[:20]


def _normalize_legacy_findings(hallazgos: list[dict[str, Any]], offset: int) -> list[NormalizedFinding]:
    out: list[NormalizedFinding] = []
    for idx, h in enumerate(hallazgos, start=offset + 1):
        severity = _legacy_severity(str(h.get("severidad") or h.get("severity") or "medium"))
        obrera = str(h.get("obrera") or "legacy_agent_worker")
        category = "workflow" if "flujo" in obrera.lower() else "prompt" if "prompt" in obrera.lower() else "agent"
        out.append(
            NormalizedFinding(
                id=f"LEG-{idx:03d}",
                severity=severity,
                category=category,  # type: ignore[arg-type]
                title=f"Hallazgo heredado de {obrera}",
                description=str(h.get("descripcion") or ""),
                file=str(h.get("ubicacion") or "") or None,
                evidence=str(h.get("ubicacion") or ""),
                recommendation=str(h.get("accion") or ""),
                auto_fix_available=bool(h.get("auto_fix_available")),
                source="legacy_agent_layer",
            )
        )
    return out


def _legacy_severity(value: str):
    return {
        "critico": "critical",
        "crítico": "critical",
        "critical": "critical",
        "alto": "high",
        "high": "high",
        "medio": "medium",
        "medium": "medium",
        "bajo": "low",
        "low": "low",
        "info": "info",
    }.get(value.strip().lower(), "medium")


def _recommendations(findings: list[NormalizedFinding]) -> list[str]:
    recs: list[str] = []
    if any(f.category == "security" and f.severity == "critical" for f in findings):
        recs.append("Bloquear salida a metadata/redes privadas y rotar secretos si alguno es real.")
    if any(f.category == "api" for f in findings):
        recs.append("Endurecer endpoints: autenticacion, timeouts, manejo de errores y schemas.")
    if any(f.category == "testing" for f in findings):
        recs.append("Agregar pruebas smoke/unitarias que no dependan de produccion.")
    if any(f.category == "documentation" for f in findings):
        recs.append("Completar README operativo con instalacion, ejecucion, env y troubleshooting.")
    if not recs:
        recs.append("Mantener evaluaciones periodicas antes de cambios de produccion.")
    return recs


def _severity_rank(value: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(value, 9)


def _summary(items: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in items.items() if v) or "sin hallazgos"


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:80] or "proyecto"
