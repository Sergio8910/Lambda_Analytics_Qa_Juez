"""Driver SINTÉTICO de PDF — genera el PDF sin disparar el flujo real.

A diferencia de `n8n_webhook` (que pega al webhook real), este driver construye
un PDF en memoria a partir de datos canónicos sintéticos (`snapshot_factory` +
`build_synthetic_pdf`). Cero red, cero webhook, cero BD: permite que la
evaluación completa SIEMPRE incluya una auditoría de PDF sin tocar producción.

Devuelve el PDF en base64 y el `expected_snapshot` usado, para que el evaluador
verifique el PDF contra exactamente esos datos.
"""
from __future__ import annotations

import base64
from typing import Any, Dict

from ..registry import driver


@driver("synthetic_pdf")
class SyntheticPdfDriver:
    """SyntheticDriver: arma un PDF sintético determinístico (sin side effects)."""

    def __init__(self, **cfg: Any) -> None:
        # Absorbe config genérica (base_url, etc.) que el orquestador inyecta.
        self.cfg = cfg or {}

    def trigger(self, synthetic_input: Dict[str, Any]) -> Dict[str, Any]:
        from juez.evaluation.contra_agente.synthetic.pdf_builder import build_synthetic_pdf
        from juez.evaluation.contra_agente.synthetic.snapshot_factory import (
            make_synthetic_data,
        )

        synthetic_input = synthetic_input or {}
        batch_id = str(self.cfg.get("batch_id") or synthetic_input.get("batch_id") or "synthetic-default")
        plan_idx = int(self.cfg.get("plan_idx", synthetic_input.get("plan_idx", 1)))

        # snapshot_factory genera PDF y expected_snapshot CONSISTENTES entre sí.
        # No sobreescribimos campos (ej. contrato_id) para no romper esa coherencia.
        expected_snapshot, canonical = make_synthetic_data(batch_id, plan_idx)

        try:
            pdf_bytes = build_synthetic_pdf(canonical, [])
        except Exception as exc:  # generación fallida → lo reporta el evaluador
            return {
                "ok": False,
                "http_status": None,
                "latency_ms": 0.0,
                "error": f"build_synthetic_pdf falló: {type(exc).__name__}: {exc}",
                "synthetic": True,
                "response": {},
                "expected_snapshot": expected_snapshot,
            }

        return {
            "ok": True,
            "http_status": None,
            "latency_ms": 0.0,
            "error": None,
            "synthetic": True,
            "response": {"pdf_base64": base64.b64encode(pdf_bytes).decode("ascii")},
            "expected_snapshot": expected_snapshot,
            "canonical": canonical,
        }
