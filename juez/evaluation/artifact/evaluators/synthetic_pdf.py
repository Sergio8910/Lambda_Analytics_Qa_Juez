"""Evaluador del PDF SINTÉTICO generado por el driver `synthetic_pdf`.

Verifica que el PDF se haya GENERADO BIEN contra el `expected_snapshot` que el
driver usó para construirlo: integridad, conteo de fotos embebidas, ambientes y
campos requeridos presentes. Como el PDF y el snapshot son consistentes, esto
audita la calidad de la GENERACIÓN del PDF (fotos que no se embeben, ambientes
ausentes, etc.) sin disparar el flujo real.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List

from .. import pdf_checks as pc
from ..protocol import ArtifactContext, ArtifactResult, Issue, mk_issue
from ..registry import evaluator


@evaluator("synthetic_pdf")
class SyntheticPdfEvaluator:
    def __init__(self, **config: Any) -> None:
        self.config = config or {}

    def evaluate(self, ctx: ArtifactContext) -> ArtifactResult:
        tr = ctx.get("trigger_result", {}) or {}
        resp = tr.get("response", {}) or {}
        expected = tr.get("expected_snapshot", {}) or {}
        problemas: List[Issue] = []
        metricas: Dict[str, Any] = {"modo": "sintetico (sin disparar el flujo real)"}

        if not tr.get("ok") or not resp.get("pdf_base64"):
            problemas.append(mk_issue(
                "CRITICO",
                f"No se generó el PDF sintético: {tr.get('error') or 'sin pdf_base64'}",
                tipo="Artefacto / PDF",
            ))
            metricas["contenido_verificado"] = False
            return {"nombre": "PDF sintético", "score": 0.0, "problemas": problemas,
                    "reporte": "", "metricas": metricas}

        blob = base64.b64decode(resp["pdf_base64"])
        scores: List[float] = []

        integ = pc.verificar_integridad(blob)
        scores.append(integ.score); problemas.extend(integ.issues); metricas.update(integ.metricas)

        counts = expected.get("counts", {}) or {}
        structure = expected.get("structure", {}) or {}

        fotos = counts.get("fotos", 0)
        if fotos:
            r = pc.verificar_conteo_fotos(blob, fotos)
            scores.append(r.score); problemas.extend(r.issues); metricas.update(r.metricas)

        ambientes = structure.get("ambientes", []) or []
        if ambientes:
            r = pc.verificar_estructura_por_ambiente(blob, ambientes)
            scores.append(r.score); problemas.extend(r.issues); metricas.update(r.metricas)

        requeridos = expected.get("required_strings", []) or []
        if requeridos:
            r = pc.verificar_campos_requeridos(blob, requeridos)
            scores.append(r.score); problemas.extend(r.issues); metricas.update(r.metricas)

        score = round(sum(scores) / len(scores) * 100, 1) if scores else 0.0
        metricas["contenido_verificado"] = True
        return {
            "nombre": "PDF sintético (e2e sin disparar)",
            "score": score,
            "problemas": problemas,
            "reporte": "",
            "metricas": metricas,
        }
