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
    "business_rule": 30.0,
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
# Tope de la penalizacion TOTAL de una categoria, como multiplo de su peso
# nominal. Antes era 1x el peso (`min(weight, penalty)`): un solo finding
# critical ya agota ese tope (weight * 1.0 == weight), asi que 1 vs 40
# findings critical en la misma categoria penalizaban exactamente igual.
# Con 4x, findings adicionales de la misma categoria siguen costando hasta
# ese multiplo, reflejando que un problema sistemico (repetido) es mas grave
# que uno aislado, sin permitir que una sola categoria borre todo el score.
_CATEGORY_PENALTY_CAP_MULTIPLIER = 4.0
# Categorias que, con un finding critical, bloquean el proyecto directo
# (status = blocked_by_critical_findings) sin importar el score numerico.
# security ya estaba; business_rule se agrega porque una violacion critica de
# una regla de negocio explicita del cliente es, para el negocio, tan grave
# como un hueco de seguridad -- no debe poder "promediarse" con otras
# categorias sanas y salir con un score alto.
_CATEGORIAS_BLOQUEANTES = {"security", "business_rule"}


_WEBHOOKS_FILENAMES = ("webhooks_n8n.json",)


def _load_declared_webhooks(root: Path) -> dict[str, str]:
    """Lee un manifiesto opcional que declara, por flujo n8n, la URL real de
    su webhook -- {"<nombre o archivo del flujo>": "https://.../webhook/..."}.

    Sin el archivo, comportamiento identico a hoy (cero llamadas reales).
    """
    for name in _WEBHOOKS_FILENAMES:
        path = root / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}
    return {}


def evaluate_project_path(
    project_path: Path | str,
    *,
    project_id: str | None = None,
    incluir_dinamicas: bool = False,
    enable_real_conversations: bool = False,
) -> ProjectEvaluationReport:
    root = Path(project_path).resolve()
    inventory = scan_project(root)
    classification = classify_project(inventory)
    findings = evaluate_project_workers(root, inventory)

    from .business_rules import business_rules_worker_findings, load_declared_purposes, run_functional_verification

    rule_findings, rules_report = business_rules_worker_findings(root, inventory)
    findings.extend(rule_findings)
    findings.extend(run_functional_verification(root, inventory, rules_report))

    if incluir_dinamicas and enable_real_conversations:
        webhooks = _load_declared_webhooks(root)
        if webhooks:
            from .conversation_check import verificar_conversaciones_reales

            for asset in inventory.assets:
                if asset.kind != "n8n_workflow":
                    continue
                path = root / asset.path
                webhook_url = webhooks.get(asset.name or "") or webhooks.get(asset.path) or webhooks.get(path.name)
                if not webhook_url:
                    continue
                try:
                    workflow = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    continue
                nombre = asset.name or path.stem
                findings.extend(verificar_conversaciones_reales(nombre, workflow, webhook_url))

    legacy_score: float | None = None
    legacy_findings: list[dict[str, Any]] = []
    cost_summary: dict[str, Any] | None = None
    legacy_components = _components_from_inventory(root, inventory)
    if legacy_components:
        purposes = load_declared_purposes(root) if incluir_dinamicas else {}
        cost_meter = _new_cost_meter() if incluir_dinamicas else None
        legacy_result = run_colmena(
            project_id or root.name,
            legacy_components,
            incluir_dinamicas=incluir_dinamicas,
            purposes=purposes,
            cost_meter=cost_meter,
        )
        legacy_score = legacy_result.score
        legacy_findings = legacy_result.hallazgos
        findings.extend(_normalize_legacy_findings(legacy_result.hallazgos, len(findings)))
        if cost_meter is not None:
            summary = cost_meter.summary()
            if summary.get("total_calls"):
                cost_summary = summary

    score = score_project(findings)
    coverage = _compute_coverage(
        root, inventory, rules_report, incluir_dinamicas=incluir_dinamicas,
    )
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
        dynamic_cost_summary=cost_summary,
        coverage=coverage,
    )


def _compute_coverage(root, inventory, rules_report, *, incluir_dinamicas: bool) -> dict[str, Any]:
    """Indice de cobertura: por cada dimension de analisis, si se EVALUO, se
    OMITIO (con motivo + como activarla) o NO_APLICA. Se deriva de hechos
    estructurados (manifiestos presentes, assets detectados, flags), no de
    adivinar por los titulos de los hallazgos.

    El objetivo es que el consumidor NUNCA confunda 'sin hallazgos' con 'todo
    revisado': aqui ve exactamente que quedo fuera y como cerrar el hueco.
    """
    from .business_rules import load_declared_purposes

    assets = inventory.detected_assets
    tiene_prompts = bool(assets.get("prompts") or assets.get("agents"))
    tiene_flujos = bool(assets.get("workflows"))
    tiene_reglas = bool(rules_report.alta_confianza())
    objetivos_declarados = bool(_load_declared_objectives(root))
    propositos_declarados = bool(load_declared_purposes(root))
    archivos_omitidos = sum(1 for a in inventory.assets if a.kind == "skipped_large_file")

    def dim(estado: str, motivo: str = "", como_activar: str = "") -> dict[str, str]:
        d = {"estado": estado}
        if motivo:
            d["motivo"] = motivo
        if como_activar:
            d["como_activar"] = como_activar
        return d

    dimensiones: dict[str, dict[str, str]] = {}
    # Seguridad estatica y arquitectura corren siempre.
    dimensiones["seguridad_estatica"] = dim("evaluada")
    dimensiones["arquitectura_calidad"] = dim("evaluada")

    dimensiones["inyeccion_prompt"] = (
        dim("evaluada") if tiene_prompts
        else dim("no_aplica", "El proyecto no tiene prompts/agentes detectados.")
    )
    dimensiones["reglas_negocio"] = (
        dim("evaluada") if tiene_reglas
        else dim("omitida", "No hay reglas de negocio explicitas de alta confianza.",
                 "Subir reglas_negocio.json con las reglas del cliente.")
    )
    if tiene_flujos:
        dimensiones["objetivos_flujos"] = (
            dim("evaluada") if objetivos_declarados
            else dim("omitida", "Los flujos n8n no declaran objetivos a verificar.",
                     "Subir objetivos_flujos.json (que debe lograr cada flujo).")
        )
    else:
        dimensiones["objetivos_flujos"] = dim("no_aplica", "El proyecto no tiene flujos n8n.")

    if tiene_prompts:
        if not incluir_dinamicas:
            dimensiones["proposito_agente"] = dim(
                "omitida", "Analisis dinamico desactivado (incluir_dinamicas=False).",
                "Correr con incluir_dinamicas=True (usa el LLM, cuesta tokens).")
        elif not propositos_declarados:
            dimensiones["proposito_agente"] = dim(
                "omitida", "No se declaro el proposito esperado de cada agente.",
                "Agregar 'proposito_por_componente' en reglas_negocio.json.")
        else:
            dimensiones["proposito_agente"] = dim("evaluada")

    dimensiones["conversaciones_dinamicas"] = (
        dim("evaluada") if incluir_dinamicas
        else dim("omitida", "No se corrieron conversaciones dinamicas.",
                 "Correr con incluir_dinamicas=True o via /evaluate/proyecto con conversaciones.")
    )

    if archivos_omitidos:
        dimensiones["cobertura_archivos"] = dim(
            "parcial", f"{archivos_omitidos} archivo(s) >2MB no analizados.",
            "Reducir su tamano (quitar datos embebidos) o analizarlos aparte.")
    else:
        dimensiones["cobertura_archivos"] = dim("completa")

    omitidas = [k for k, v in dimensiones.items() if v["estado"] == "omitida"]
    parciales = [k for k, v in dimensiones.items() if v["estado"] in {"parcial"}]
    evaluadas = [k for k, v in dimensiones.items() if v["estado"] in {"evaluada", "completa"}]
    return {
        "completa": not omitidas and not parciales,
        "dimensiones": dimensiones,
        "resumen": {
            "evaluadas": len(evaluadas),
            "omitidas": len(omitidas),
            "parciales": len(parciales),
            "omitidas_detalle": [
                {"dimension": k, "motivo": dimensiones[k].get("motivo", ""),
                 "como_activar": dimensiones[k].get("como_activar", "")}
                for k in omitidas + parciales
            ],
        },
    }


def _new_cost_meter():
    try:
        from juez.evaluation.contra_agente.synthetic.cost_meter import CostMeter

        return CostMeter()
    except Exception:
        return None


def score_project(findings: list[NormalizedFinding]) -> ProjectScore:
    by_severity = Counter(f.severity for f in findings)
    by_category = Counter(f.category for f in findings)
    penalties_by_category: dict[str, float] = {}
    for finding in findings:
        weight = _CATEGORY_WEIGHTS.get(finding.category, 5.0)
        penalties_by_category[finding.category] = penalties_by_category.get(finding.category, 0.0) + (
            weight * _SEVERITY_IMPACT[finding.severity]
        )
    weighted_penalty = min(
        100.0,
        sum(
            min(_CATEGORY_WEIGHTS.get(cat, 5.0) * _CATEGORY_PENALTY_CAP_MULTIPLIER, penalty)
            for cat, penalty in penalties_by_category.items()
        ),
    )
    score = round(max(0.0, 100.0 - weighted_penalty), 1)
    critical_bloqueante = any(f.severity == "critical" and f.category in _CATEGORIAS_BLOQUEANTES for f in findings)
    critical_count = by_severity.get("critical", 0)
    high_count = by_severity.get("high", 0)
    if critical_bloqueante:
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
    if report.dynamic_cost_summary:
        s = report.dynamic_cost_summary
        lines.append("")
        lines.append(
            f"  COSTO OBRERAS DINAMICAS: {s.get('total_calls', 0)} llamada(s), "
            f"{s.get('total_tokens', 0)} tokens, USD ~{s.get('total_cost_usd', 0)}"
        )
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


_OBJECTIVES_FILENAMES = ("objetivos_flujos.json", "objectives.json")


def _load_declared_objectives(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Lee un manifiesto opcional que declara objetivos por flujo n8n.

    Formato: {"<nombre o archivo del flujo>": [{"id": ..., "descripcion": ..., "kind": ...}, ...]}
    Sin el archivo, comportamiento identico a hoy (objetivos vacios, sin regresion).
    """
    for name in _OBJECTIVES_FILENAMES:
        path = root / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, list)}
    return {}


def _components_from_inventory(root: Path, inventory) -> list[Componente]:
    components: list[Componente] = []
    declared_objectives = _load_declared_objectives(root)
    for asset in inventory.assets:
        path = root / asset.path
        if asset.kind == "n8n_workflow":
            try:
                workflow = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            nombre = asset.name or path.stem
            objetivos = declared_objectives.get(nombre) or declared_objectives.get(asset.path) or declared_objectives.get(path.name) or []
            components.append(
                Componente(
                    kind="n8n",
                    nombre=nombre,
                    workflow_json=workflow,
                    workflow_path=str(path),
                    objetivos=objetivos,
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
