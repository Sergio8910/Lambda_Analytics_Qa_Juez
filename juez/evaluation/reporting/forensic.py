from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..report_models import EvaluationSpec, RunReport


def build_forensic_report(
    run_report: RunReport,
    spec: EvaluationSpec,
    audit_mode: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> Dict[str, Any]:
    summary = run_report.summary
    executive = summary.executive_summary or {}

    tag_counts = Counter()
    severity_counts = Counter()
    for case in run_report.cases:
        for t in case.tags:
            tag_counts[t] += 1
        severity_counts[case.severity.lower()] += 1

    gating_failures = []
    for case in run_report.cases:
        for item in case.gating_metrics_resultado or []:
            if item.get("success") is False:
                gating_failures.append(
                    {
                        "metric": item.get("name"),
                        "case_id": case.case_id,
                        "reason": item.get("reason") if isinstance(item, dict) else None,
                    }
                )

    main_failures = list(executive.get("main_failures") or [])
    if not main_failures:
        main_failures = [
            f"{k}: {v}" for k, v in (summary.by_metric_failures or {}).items()
        ][:3]
    if not main_failures:
        main_failures = ["Sin fallos críticos detectados."]

    recommended_actions = list(executive.get("recommended_actions") or [])
    if not recommended_actions:
        recommended_actions = [
            "Reforzar cobertura de casos críticos.",
            "Revisar métricas de calidad con fallos recurrentes.",
            "Mantener monitoreo continuo de resultados.",
        ]

    production_ready = None
    if executive:
        production_ready = executive.get("verdict") == "CUMPLE"

    failed_cases = [c for c in run_report.cases if not c.passed]
    severity_rank = {"alta": 3, "media": 2, "baja": 1}
    failed_cases_sorted = sorted(
        failed_cases,
        key=lambda c: (
            -severity_rank.get(c.severity.lower(), 0),
            c.scorecard.get("overall_score", 1.0)
            if isinstance(c.scorecard, dict) and c.scorecard.get("overall_score") is not None
            else 1.0,
        ),
    )

    representative_failed_cases: List[Dict[str, Any]] = []
    for case in failed_cases_sorted[:5]:
        metrics_fallidas = []
        for m in case.metrics:
            if m.success is False:
                metrics_fallidas.append(
                    {
                        "name": m.name,
                        "score": m.score,
                        "reason_es": m.reason_es or m.reason,
                        "skipped": m.skipped,
                        "skip_reason": m.skip_reason,
                    }
                )
        output_text = ""
        if case.normalized_run and isinstance(case.normalized_run, dict):
            output_text = (
                case.normalized_run.get("execution", {})
                .get("output_text", "")
            )
        if not output_text and case.turns:
            output_text = case.turns[-1].agent_output
        representative_failed_cases.append(
            {
                "case_id": case.case_id,
                "tags": case.tags,
                "severity": case.severity,
                "input": case.normalized_run.get("input", {}).get("user_message") if case.normalized_run else case.case_id,
                "context": case.normalized_run.get("context", {}).get("retrieval_context")
                if case.normalized_run
                else [],
                "output_text": output_text,
                "failed_metrics": metrics_fallidas,
                "primary_fail_reasons": (case.feedback.overall.primary_fail_reasons if case.feedback else []),
            }
        )

    forensic = {
        "meta": {
            "run_id": summary.run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "audit_mode": audit_mode or spec.audit_mode,
            "spec_path": spec_path,
            "agent_module": spec.agent_module,
            "agent_function": spec.agent_function,
            "agent_type": spec.agent_type,
        },
        "input_analysis": {
            "tag_distribution": dict(tag_counts),
            "severity_distribution": dict(severity_counts),
            "notes": [],
        },
        "test_plan": {
            "gating_metrics": spec.gating_metrics or [],
            "diagnostic_metrics": spec.diagnostic_metrics or [],
            "metrics_enabled": [m.name for m in spec.metrics if m.enabled],
        },
        "generated_cases": [],
        "evaluation_results": {
            "pass_rate": summary.pass_rate,
            "failed_cases": summary.failed_cases,
            "by_metric_failures": summary.by_metric_failures,
            "reliability_score": summary.reliability_score,
            "completeness_score": summary.completeness_score,
        },
        "failure_analysis": {
            "gating_failures": gating_failures,
            "main_failures": main_failures,
            "representative_failed_cases": representative_failed_cases,
        },
        "prompt_rewrite_recommendations": {
            "notes": [],
            "recommended_actions": recommended_actions,
        },
        "risk_assessment": {
            "risk_level": executive.get("risk_level"),
            "risk_score": executive.get("risk_score"),
            "production_ready": production_ready,
        },
        "executive_summary": executive,
        "notes": [],
    }

    if not run_report.cases:
        forensic["notes"].append("not_available: cases")
    if not executive:
        forensic["notes"].append("not_available: executive_summary")
    if not forensic["generated_cases"]:
        forensic["notes"].append("not_available: generated_cases")
    return forensic


def render_forensic_pdf(forensic: Dict[str, Any], out_path: Any) -> None:
    try:
        import reportlab  # noqa: F401
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError(
            "PDF generation requires reportlab. Install with: pip install reportlab"
        ) from exc

    target = out_path
    if isinstance(out_path, (str, Path)):
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        target = str(out)

    doc = SimpleDocTemplate(
        target,
        pagesize=LETTER,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()
    story: List[Any] = []

    def _table(rows: List[List[Any]], col_widths: Optional[List[int]] = None) -> Table:
        table = Table(rows, colWidths=col_widths)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ]
            )
        )
        return table

    def _bullets(items: List[str]) -> List[Paragraph]:
        if not items:
            return [Paragraph("- Sin datos.", styles["BodyText"]) ]
        return [Paragraph(f"- {i}", styles["BodyText"]) for i in items]

    meta = forensic.get("meta", {})
    exec_sum = forensic.get("executive_summary", {}) or {}
    summary = forensic.get("evaluation_results", {}) or {}

    title = f"Lambda AI Judge - Reporte Forense ({meta.get('run_id', 'sin_run_id')})"
    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 0.2 * inch))

    portada = [
        ["Fecha", meta.get("generated_at", "")],
        ["Audit mode", meta.get("audit_mode", "")],
        ["Verdict", exec_sum.get("verdict", "")],
        ["Scorecard global", _fmt(exec_sum.get("scorecard_global"))],
        ["Pass rate", _fmt(summary.get("pass_rate"))],
    ]
    story.append(_table(portada))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Scorecard / Dimensiones", styles["Heading2"]))
    dims = _extract_dimensions(forensic)
    if dims:
        story.append(_table([["Dimensión", "Score"]] + dims))
    else:
        story.append(Paragraph("No hay dimensiones disponibles.", styles["BodyText"]))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Top fallas y acciones recomendadas", styles["Heading2"]))
    failures = forensic.get("failure_analysis", {}).get("main_failures", [])
    actions = forensic.get("prompt_rewrite_recommendations", {}).get("recommended_actions", [])
    story.append(Paragraph("Fallas principales:", styles["BodyText"]))
    story.extend(_bullets(failures))
    story.append(Paragraph("Acciones recomendadas:", styles["BodyText"]))
    story.extend(_bullets(actions))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Casos fallidos", styles["Heading2"]))
    failed_table = _failed_cases_table(forensic)
    if failed_table:
        story.append(_table(failed_table))
    else:
        story.append(Paragraph("No se registran casos fallidos.", styles["BodyText"]))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Detalle técnico", styles["Heading2"]))
    tech_table = _technical_table(forensic)
    if tech_table:
        story.append(_table(tech_table, col_widths=[160, 70, 70, 70]))
    else:
        story.append(Paragraph("No hay detalle técnico disponible.", styles["BodyText"]))

    footer = f"Spec: {meta.get('spec_path') or 'no_disponible'}"
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(footer, styles["Italic"]))

    doc.build(story)


def _fmt(val: Any) -> str:
    if val is None:
        return "n/a"
    if isinstance(val, float):
        return f"{val:.2f}"
    return str(val)



def _extract_dimensions(forensic: Dict[str, Any]) -> List[List[str]]:
    # Extrae desde executive_summary si no hay dimensiones completas.
    exec_sum = forensic.get("executive_summary", {}) or {}
    dims = []
    scorecard_global = exec_sum.get("scorecard_global")
    if scorecard_global is not None:
        dims.append(["scorecard_global", _fmt(scorecard_global)])
    # Si hay dimensiones reales en el report (no guardadas aquí), queda como n/a
    return dims


def _failed_cases_table(forensic: Dict[str, Any]) -> List[List[str]]:
    results = forensic.get("evaluation_results", {}) or {}
    if results.get("failed_cases", 0) == 0:
        return []
    # No tenemos casos completos aquí, solo un resumen.
    return [
        ["case_id", "severidad", "tags", "razones"],
        ["(ver JSON completo)", "n/a", "n/a", "ver failure_analysis"],
    ]


def _technical_table(forensic: Dict[str, Any]) -> List[List[str]]:
    # Sin detalle de métricas por caso en este bloque, dejar referencia.
    return [
        ["case_id", "métrica", "score", "success"],
        ["(ver JSON completo)", "-", "-", "-"],
    ]
