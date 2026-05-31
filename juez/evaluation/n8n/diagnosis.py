from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from .models import (
    N8nDiagnosis,
    N8nDiagnosisFinding,
    N8nFinding,
    N8nWorkflowAnalysis,
)

DiagnosisMode = Literal["auto", "llm", "fallback"]

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_CATEGORY_IMPORTANCE = {
    "security": 0,
    "logic": 1,
    "structure": 2,
    "operations": 3,
    "redundancy": 4,
    "maintainability": 5,
}
_WHY_IT_MATTERS = {
    "security": "Puede exponer credenciales, datos sensibles o abrir superficies de ataque innecesarias.",
    "logic": "Puede romper la ejecución o producir resultados incorrectos aunque el workflow parezca válido.",
    "structure": "Deja ramas fuera del flujo principal o reduce la cobertura real del proceso.",
    "operations": "Aumenta la probabilidad de caídas, errores intermitentes y recuperaciones incompletas.",
    "redundancy": "Eleva el costo de mantenimiento y crea riesgo de comportamientos inconsistentes entre ramas similares.",
    "maintainability": "Hace más lenta la depuración y más riesgosa la evolución del flujo.",
}


def analyze_workflow_with_diagnosis(
    workflow: Dict[str, Any],
    *,
    include_graph: bool = True,
    diagnosis_mode: DiagnosisMode = "auto",
    diagnosis_model: Optional[str] = None,
    diagnosis_max_tokens: int = 900,
    diagnosis_temperature: float = 0.1,
) -> Tuple[N8nWorkflowAnalysis, List[str]]:
    from .static_analysis import analyze_workflow

    analysis = analyze_workflow(workflow, include_graph=include_graph)
    diagnosis, warnings = build_workflow_diagnosis(
        analysis,
        mode=diagnosis_mode,
        model=diagnosis_model,
        max_tokens=diagnosis_max_tokens,
        temperature=diagnosis_temperature,
    )
    return analysis.model_copy(update={"diagnosis": diagnosis}), warnings


def build_workflow_diagnosis(
    analysis: N8nWorkflowAnalysis,
    *,
    mode: DiagnosisMode = "auto",
    model: Optional[str] = None,
    max_tokens: int = 900,
    temperature: float = 0.1,
) -> Tuple[N8nDiagnosis, List[str]]:
    warnings: List[str] = []
    resolved_model = model or os.getenv("N8N_DIAGNOSIS_MODEL") or os.getenv("JUDGE_MODEL") or "gpt-4o-mini"

    if mode in {"auto", "llm"}:
        if os.getenv("OPENAI_API_KEY"):
            try:
                diagnosis = _build_diagnosis_llm(
                    analysis,
                    model=resolved_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return diagnosis, warnings
            except Exception as exc:
                warnings.append(f"Diagnóstico n8n LLM no generado: {exc}")
        elif mode == "llm":
            warnings.append("Diagnóstico n8n LLM no generado: OPENAI_API_KEY no está configurada.")

    diagnosis = _build_diagnosis_fallback(analysis)
    return diagnosis, warnings


def _build_diagnosis_llm(
    analysis: N8nWorkflowAnalysis,
    *,
    model: str,
    max_tokens: int,
    temperature: float,
) -> N8nDiagnosis:
    from openai import OpenAI

    prompt = _build_llm_prompt(analysis)
    client = OpenAI(timeout=30, max_retries=0)
    if hasattr(client, "responses"):
        resp = client.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
        raw_text = getattr(resp, "output_text", "") or ""
    else:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        raw_text = resp.choices[0].message.content if resp.choices else ""

    payload = _extract_json_payload(raw_text)
    data = json.loads(payload)
    data["source"] = "llm"
    data["model"] = model
    diagnosis = N8nDiagnosis.model_validate(data)
    return _coerce_diagnosis_lists(diagnosis)


def _build_diagnosis_fallback(analysis: N8nWorkflowAnalysis) -> N8nDiagnosis:
    priority_findings = _priority_findings(analysis.findings)
    risk_level = _risk_level(analysis)
    verdict = _verdict(analysis, risk_level)
    confidence = _confidence(analysis)
    strengths = _derive_strengths(analysis)
    failure_modes = _derive_failure_modes(analysis)
    redundancies = _derive_redundancies(analysis)
    recommended_actions = _derive_recommendations(analysis)
    unknowns = _derive_unknowns(analysis)
    executive_summary = _build_executive_summary(
        analysis,
        verdict=verdict,
        risk_level=risk_level,
        priority_findings=priority_findings,
    )
    return N8nDiagnosis(
        source="fallback",
        model=None,
        verdict=verdict,
        risk_level=risk_level,
        confidence=confidence,
        executive_summary=executive_summary,
        strengths=strengths,
        failure_modes=failure_modes,
        redundancies=redundancies,
        recommended_actions=recommended_actions,
        priority_findings=priority_findings,
        unknowns=unknowns,
    )


def _build_llm_prompt(analysis: N8nWorkflowAnalysis) -> str:
    summary = {
        "workflow": {
            "name": analysis.inventory.workflow_name,
            "total_nodes": analysis.inventory.total_nodes,
            "total_edges": analysis.inventory.total_edges,
            "trigger_nodes": analysis.inventory.trigger_nodes,
            "webhook_nodes": analysis.inventory.webhook_nodes,
            "http_nodes": analysis.inventory.http_nodes[:12],
            "ai_nodes": analysis.inventory.ai_nodes[:12],
            "code_nodes": analysis.inventory.code_nodes[:12],
        },
        "scorecard": analysis.scorecard.model_dump(mode="json"),
        "counts_by_severity": analysis.counts_by_severity,
        "counts_by_category": analysis.counts_by_category,
        "top_findings": [
            {
                "finding_id": finding.finding_id,
                "category": finding.category,
                "severity": finding.severity,
                "title": finding.title,
                "message": finding.message,
                "node_names": finding.node_names[:10],
                "recommendation": finding.recommendation,
                "evidence": _compact_evidence(finding.evidence),
            }
            for finding in _sort_findings(analysis.findings)[:12]
        ],
        "static_scope": {
            "mode": "static_analysis",
            "limitations": [
                "No hay trazas de ejecución real.",
                "No se validaron respuestas reales de LLM ni resultados HTTP en vivo.",
                "No se ejecutaron subworkflows externos.",
            ],
        },
    }
    schema = {
        "verdict": "string breve en español",
        "risk_level": "low | medium | high | critical",
        "confidence": "low | medium | high",
        "executive_summary": "string",
        "strengths": ["string"],
        "failure_modes": ["string"],
        "redundancies": ["string"],
        "recommended_actions": ["string"],
        "priority_findings": [
            {
                "finding_id": "string",
                "title": "string",
                "severity": "critical | high | medium | low | info",
                "why_it_matters": "string",
                "node_names": ["string"],
            }
        ],
        "unknowns": ["string"],
    }
    return (
        "Eres el motor interno del Juez para auditar workflows n8n. "
        "Tu trabajo es diagnosticar el flujo usando SOLAMENTE la evidencia recibida. "
        "No inventes nodos, endpoints ni fallos que no estén soportados por la evidencia. "
        "Si algo solo puede confirmarse ejecutando el flujo, debes decirlo en unknowns. "
        "Responde solo JSON válido, sin markdown ni texto adicional.\n\n"
        "Prioriza:\n"
        "1. Riesgos reales de seguridad, lógica y operación.\n"
        "2. Dónde es probable que falle el flujo.\n"
        "3. Dónde hay redundancia o deuda técnica.\n"
        "4. Qué debe corregirse primero.\n\n"
        f"Schema esperado:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Evidencia:\n{json.dumps(summary, ensure_ascii=False)}"
    )


def _extract_json_payload(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Respuesta LLM vacía.")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No se encontró un objeto JSON válido en la respuesta LLM.")
    return text[start : end + 1]


def _compact_evidence(evidence: Dict[str, Any], limit: int = 4) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    for idx, (key, value) in enumerate((evidence or {}).items()):
        if idx >= limit:
            compact["truncated"] = True
            break
        if isinstance(value, dict):
            compact[key] = {str(k): v for k, v in list(value.items())[:3]}
        elif isinstance(value, list):
            compact[key] = value[:5]
        else:
            compact[key] = value
    return compact


def _sort_findings(findings: List[N8nFinding]) -> List[N8nFinding]:
    return sorted(
        findings,
        key=lambda finding: (
            _SEVERITY_RANK.get(finding.severity, 99),
            _CATEGORY_IMPORTANCE.get(finding.category, 99),
            finding.title.lower(),
        ),
    )


def _priority_findings(findings: List[N8nFinding]) -> List[N8nDiagnosisFinding]:
    priority: List[N8nDiagnosisFinding] = []
    for finding in _sort_findings(findings)[:5]:
        priority.append(
            N8nDiagnosisFinding(
                finding_id=finding.finding_id,
                title=finding.title,
                severity=finding.severity,
                why_it_matters=_WHY_IT_MATTERS.get(
                    finding.category,
                    "Tiene impacto material en la calidad, estabilidad o seguridad del workflow.",
                ),
                node_names=finding.node_names[:10],
            )
        )
    return priority


def _risk_level(analysis: N8nWorkflowAnalysis) -> Literal["low", "medium", "high", "critical"]:
    counts = analysis.counts_by_severity or {}
    overall = analysis.scorecard.overall
    if counts.get("critical", 0) > 0:
        return "critical"
    if counts.get("high", 0) >= 2 or overall < 0.7 or analysis.scorecard.status == "fail":
        return "high"
    if counts.get("high", 0) == 1 or counts.get("medium", 0) >= 2 or overall < 0.9:
        return "medium"
    return "low"


def _verdict(
    analysis: N8nWorkflowAnalysis,
    risk_level: Literal["low", "medium", "high", "critical"],
) -> str:
    if risk_level == "critical":
        return "Bloqueado por riesgos críticos"
    if risk_level == "high":
        return "No listo para producción"
    if risk_level == "medium":
        return "Requiere correcciones antes de escalar"
    if analysis.findings:
        return "Operable con observaciones"
    return "Saludable en análisis estático"


def _confidence(analysis: N8nWorkflowAnalysis) -> Literal["low", "medium", "high"]:
    if analysis.inventory.total_nodes >= 8 and analysis.findings:
        return "medium"
    if analysis.inventory.total_nodes >= 8 and not analysis.findings:
        return "high"
    return "low"


def _derive_strengths(analysis: N8nWorkflowAnalysis) -> List[str]:
    strengths: List[str] = []
    severity_counts = analysis.counts_by_severity or {}
    finding_ids = {finding.finding_id for finding in analysis.findings}
    if not severity_counts.get("critical") and not severity_counts.get("high"):
        strengths.append("No se detectaron hallazgos críticos o altos en el análisis estático.")
    if "logic-broken-node-references" not in finding_ids and "structure-unreachable-nodes" not in finding_ids:
        strengths.append("La estructura principal del flujo parece consistente y alcanzable desde sus triggers.")
    if not any(f.category == "redundancy" for f in analysis.findings):
        strengths.append("No se observaron duplicidades evidentes de HTTP, código o prompts.")
    if analysis.scorecard.security_posture >= 0.9:
        strengths.append("La postura de seguridad está bien puntuada dentro del alcance estático actual.")
    if analysis.scorecard.operational_resilience >= 0.9:
        strengths.append("La resiliencia operativa no muestra fragilidades importantes en la configuración visible.")
    return strengths[:4] or ["El workflow tiene estructura suficiente para ser inspeccionado y automatizar un diagnóstico reproducible."]


def _derive_failure_modes(analysis: N8nWorkflowAnalysis) -> List[str]:
    modes: List[str] = []
    for finding in _sort_findings(analysis.findings):
        if finding.category not in {"security", "logic", "structure", "operations"}:
            continue
        modes.append(f"{finding.title}: {finding.message}")
        if len(modes) >= 5:
            break
    if not modes:
        modes.append("No se identificaron modos de fallo estáticos claros dentro de la evidencia analizada.")
    return modes


def _derive_redundancies(analysis: N8nWorkflowAnalysis) -> List[str]:
    redundancies = [
        f"{finding.title}: {finding.message}"
        for finding in _sort_findings(analysis.findings)
        if finding.category == "redundancy"
    ]
    return redundancies[:4] or ["No se detectaron redundancias obvias en llamadas HTTP, código o prompts."]


def _derive_recommendations(analysis: N8nWorkflowAnalysis) -> List[str]:
    recommendations: List[str] = []
    for finding in _sort_findings(analysis.findings):
        rec = finding.recommendation.strip()
        if rec and rec not in recommendations:
            recommendations.append(rec)
        if len(recommendations) >= 6:
            break
    if not recommendations:
        recommendations.append("Complementa el análisis estático con ejecuciones controladas y trazas por nodo.")
    if "Complementa el análisis estático con ejecuciones controladas y trazas por nodo." not in recommendations:
        recommendations.append("Complementa el análisis estático con ejecuciones controladas y trazas por nodo.")
    return recommendations[:6]


def _derive_unknowns(analysis: N8nWorkflowAnalysis) -> List[str]:
    unknowns = [
        "Este diagnóstico no ejecuta el workflow; no confirma timeouts, respuestas HTTP reales ni errores runtime.",
        "Si el flujo usa prompts o nodos IA, todavía no se validó grounding, calidad de respuesta ni costo real por ejecución.",
    ]
    if analysis.inventory.http_nodes:
        unknowns.append("No se verificó contrato real de las APIs externas ni autenticación en vivo.")
    if analysis.inventory.ai_nodes:
        unknowns.append("No se probaron ataques de prompt injection ni regresiones de contexto en ejecución real.")
    if analysis.inventory.total_nodes >= 25:
        unknowns.append("Conviene revisar subflujos y rutas menos frecuentes con trazas reales para confirmar cobertura completa.")
    return unknowns[:5]


def _build_executive_summary(
    analysis: N8nWorkflowAnalysis,
    *,
    verdict: str,
    risk_level: str,
    priority_findings: List[N8nDiagnosisFinding],
) -> str:
    counts = analysis.counts_by_severity or {}
    main_titles = ", ".join(item.title for item in priority_findings[:3]) or "sin hallazgos dominantes"
    return (
        f"{verdict}. El análisis estático encontró {len(analysis.findings)} hallazgos "
        f"({counts.get('critical', 0)} críticos, {counts.get('high', 0)} altos, {counts.get('medium', 0)} medios) "
        f"en un workflow de {analysis.inventory.total_nodes} nodos. "
        f"Riesgo {risk_level}. Los focos principales son: {main_titles}."
    )


def _coerce_diagnosis_lists(diagnosis: N8nDiagnosis) -> N8nDiagnosis:
    return diagnosis.model_copy(
        update={
            "strengths": diagnosis.strengths[:4],
            "failure_modes": diagnosis.failure_modes[:5],
            "redundancies": diagnosis.redundancies[:4],
            "recommended_actions": diagnosis.recommended_actions[:6],
            "priority_findings": diagnosis.priority_findings[:5],
            "unknowns": diagnosis.unknowns[:5],
        }
    )
