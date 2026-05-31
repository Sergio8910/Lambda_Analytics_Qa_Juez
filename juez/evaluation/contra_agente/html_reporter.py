"""HTML reporter del contra-agente.

Produce un HTML self-contained (CSS inline, sin recursos externos) con el
resultado de un BatchResult. Soporta:
  - Score global con barra visual.
  - Tarjetas por metrica del scorecard.
  - Seccion e2e con verdict y checks si hay artifact_verdict.
  - Lista de problemas por severidad si se pasa `analisis`.
  - Bloque de diagnostico narrativo opcional (`diagnostic_text`).
  - Tokens consumidos si `batch_result.cost_summary` esta presente.
  - Estilos para impresion (`@media print`).
"""
from __future__ import annotations

import html as _html
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import BatchResult, ConversationResult


# ---------------------------------------------------------------------------
# Paleta
# ---------------------------------------------------------------------------

_COLOR_BG = "#0f172a"
_COLOR_ACCENT = "#2563eb"
_COLOR_OK = "#16a34a"
_COLOR_WARN = "#d97706"
_COLOR_FAIL = "#dc2626"


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------


def _css() -> str:
    return f"""
* {{ box-sizing: border-box; }}
html, body {{
  margin: 0;
  padding: 0;
  background: {_COLOR_BG};
  color: #e2e8f0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 14px;
  line-height: 1.5;
}}
header {{
  background: {_COLOR_ACCENT};
  color: white;
  padding: 24px 32px;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: 0.3px;
}}
section {{
  padding: 24px 32px;
  border-bottom: 1px solid #1e293b;
}}
section h2 {{
  margin: 0 0 16px;
  font-size: 16px;
  text-transform: uppercase;
  color: #93c5fd;
  letter-spacing: 0.5px;
}}
.score-global .bar-wrap {{
  background: #1e293b;
  border-radius: 8px;
  height: 28px;
  overflow: hidden;
  position: relative;
}}
.score-global .bar-fill {{
  background: linear-gradient(90deg, {_COLOR_ACCENT}, #60a5fa);
  height: 100%;
}}
.score-global .bar-label {{
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  color: white;
}}
.dimensiones .grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}}
.dim-card {{
  background: #1e293b;
  padding: 12px 14px;
  border-radius: 8px;
  border-left: 4px solid {_COLOR_ACCENT};
}}
.dim-card .name {{
  font-size: 12px;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}}
.dim-card .value {{
  font-size: 22px;
  font-weight: 600;
  margin: 4px 0 6px;
}}
.dim-card .mini-bar {{
  height: 6px;
  background: #0f172a;
  border-radius: 4px;
  overflow: hidden;
}}
.dim-card .mini-bar > div {{
  height: 100%;
  background: {_COLOR_ACCENT};
}}
.e2e .case {{
  background: #1e293b;
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 10px;
}}
.e2e .case .verdict {{
  font-weight: 600;
  margin-right: 8px;
}}
.e2e .check {{
  display: block;
  font-size: 13px;
  color: #cbd5e1;
  margin-top: 4px;
}}
.problemas .item {{
  background: #1e293b;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 8px;
  border-left: 4px solid {_COLOR_WARN};
}}
.problemas .item.sev-alta {{ border-left-color: {_COLOR_FAIL}; }}
.problemas .item.sev-media {{ border-left-color: {_COLOR_WARN}; }}
.problemas .item.sev-baja {{ border-left-color: {_COLOR_OK}; }}
.problemas .sev-label {{
  display: inline-block;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
  margin-right: 6px;
  background: rgba(255,255,255,0.1);
  text-transform: uppercase;
}}
.diagnostico {{
  white-space: pre-wrap;
  background: #1e293b;
  padding: 16px;
  border-radius: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  line-height: 1.55;
}}
.cost {{
  font-size: 13px;
  color: #cbd5e1;
}}
footer {{
  padding: 16px 32px;
  font-size: 12px;
  color: #64748b;
}}
.ok   {{ color: {_COLOR_OK}; }}
.warn {{ color: {_COLOR_WARN}; }}
.fail {{ color: {_COLOR_FAIL}; }}
@media print {{
  body {{ background: white; color: #111827; }}
  header {{ background: white; color: {_COLOR_ACCENT}; border-bottom: 2px solid {_COLOR_ACCENT}; }}
  section {{ border-bottom: 1px solid #e5e7eb; page-break-inside: avoid; }}
  section h2 {{ color: {_COLOR_ACCENT}; }}
  .dim-card, .e2e .case, .problemas .item, .diagnostico {{
    background: #f8fafc; color: #111827; border-color: #e5e7eb;
  }}
  .score-global .bar-wrap {{ background: #e5e7eb; }}
  .score-global .bar-label {{ color: #111827; }}
}}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _esc(s: Any) -> str:
    return _html.escape(str(s) if s is not None else "", quote=True)


def _pct(value: float) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "0%"


def _bar(value: float, label: Optional[str] = None) -> str:
    try:
        v = max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        v = 0.0
    pct = v * 100
    label_html = _esc(label) if label is not None else f"{pct:.0f}%"
    return (
        f'<div class="bar-wrap">'
        f'<div class="bar-fill" style="width: {pct:.1f}%"></div>'
        f'<div class="bar-label">{label_html}</div>'
        f'</div>'
    )


def _dim_cards(scorecard: Dict[str, Any]) -> str:
    if not scorecard:
        return '<p>Sin metricas agregadas para mostrar.</p>'
    cards: List[str] = []
    for metric, value in scorecard.items():
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.0
        v_clamped = max(0.0, min(1.0, v))
        cards.append(
            '<div class="dim-card">'
            f'<div class="name">{_esc(metric)}</div>'
            f'<div class="value">{v * 100:.0f}%</div>'
            f'<div class="mini-bar"><div style="width: {v_clamped * 100:.1f}%"></div></div>'
            '</div>'
        )
    return f'<div class="grid">{"".join(cards)}</div>'


def _e2e_section(results: List[ConversationResult]) -> Optional[str]:
    artifact_cases = [r for r in results if getattr(r, "artifact_verdict", None)]
    if not artifact_cases:
        return None

    parts: List[str] = []
    for cr in artifact_cases:
        verdict = cr.artifact_verdict or {}
        v_label = (verdict.get("verdict") or verdict.get("status") or "?").upper()
        v_cls = "ok" if v_label in ("OK", "PASS", "APROBADO", "COMPLETED") else (
            "warn" if v_label in ("SKIPPED", "DEGRADED") else "fail"
        )
        score = verdict.get("score")
        score_txt = ""
        if score is not None:
            try:
                score_txt = f' score={float(score) * 100:.0f}%'
            except (TypeError, ValueError):
                pass
        latency = verdict.get("elapsed_ms")
        lat_txt = f' latencia={int(latency)}ms' if latency is not None else ""
        artifact_id = verdict.get("artifact_id") or verdict.get("metadata", {}).get("artifact_id", "")
        header = (
            f'<span class="verdict {v_cls}">{_esc(v_label)}</span>'
            f'<strong>{_esc(cr.plan_id)}</strong>'
            f'{_esc(score_txt)}{_esc(lat_txt)}'
        )
        if artifact_id:
            header += f' <small>artifact_id={_esc(artifact_id)}</small>'
        check_lines: List[str] = []
        checks = verdict.get("checks") or []
        for ch in checks[:8]:
            ch_name = ch.get("name", "?")
            ch_verdict = (ch.get("verdict") or "?").upper()
            ch_score = ch.get("score")
            ch_score_txt = ""
            if ch_score is not None:
                try:
                    ch_score_txt = f" ({float(ch_score) * 100:.0f}%)"
                except (TypeError, ValueError):
                    pass
            cls = "ok" if ch_verdict in ("OK", "PASS") else (
                "warn" if ch_verdict in ("WARN", "DEGRADED") else "fail"
            )
            check_lines.append(
                f'<span class="check"><span class="{cls}">[{_esc(ch_verdict)}]</span> '
                f'{_esc(ch_name)}{_esc(ch_score_txt)}</span>'
            )
        case_html = f'<div class="case">{header}{"".join(check_lines)}</div>'
        parts.append(case_html)

    return (
        '<section class="e2e"><h2>Verificacion e2e</h2>'
        + "".join(parts)
        + "</section>"
    )


def _problemas_section(analisis: Optional[Dict[str, Any]]) -> Optional[str]:
    if not analisis or not isinstance(analisis, dict):
        return None
    problemas = analisis.get("problemas") or analisis.get("issues") or []
    if not problemas:
        return None
    items: List[str] = []
    # Ordenar por severidad: alta primero
    sev_order = {"alta": 0, "media": 1, "baja": 2}
    try:
        problemas_sorted = sorted(
            problemas,
            key=lambda p: sev_order.get((p.get("severidad") or p.get("severity") or "media").lower(), 1),
        )
    except Exception:  # noqa: BLE001
        problemas_sorted = problemas
    for p in problemas_sorted:
        sev = (p.get("severidad") or p.get("severity") or "media").lower()
        msg = p.get("mensaje") or p.get("message") or p.get("descripcion") or ""
        items.append(
            f'<div class="item sev-{_esc(sev)}">'
            f'<span class="sev-label">{_esc(sev)}</span>'
            f'{_esc(msg)}'
            "</div>"
        )
    return (
        '<section class="problemas"><h2>Problemas detectados</h2>'
        + "".join(items)
        + "</section>"
    )


def _cost_section(cost_summary: Optional[Dict[str, Any]]) -> Optional[str]:
    if not cost_summary:
        return None
    if not isinstance(cost_summary, dict):
        return None
    total_tokens = cost_summary.get("total_tokens", 0)
    total_cost = cost_summary.get("total_cost_usd") or 0.0
    total_calls = cost_summary.get("total_calls", 0)
    try:
        total_cost_f = float(total_cost)
    except (TypeError, ValueError):
        total_cost_f = 0.0
    parts = [
        f'<p class="cost">Tokens totales: <strong>{_esc(total_tokens)}</strong> '
        f'(${total_cost_f:.4f} USD estimados, {_esc(total_calls)} llamadas).</p>'
    ]
    by_model = cost_summary.get("by_model") or {}
    if isinstance(by_model, dict) and by_model:
        rows = []
        for model_name, data in by_model.items():
            if not isinstance(data, dict):
                continue
            p_tok = data.get("prompt_tokens", 0)
            c_tok = data.get("completion_tokens", 0)
            calls = data.get("calls", 0)
            cost = data.get("cost_usd") or 0.0
            try:
                cost_f = float(cost)
            except (TypeError, ValueError):
                cost_f = 0.0
            rows.append(
                f'<li class="cost"><strong>{_esc(model_name)}</strong>: '
                f'{_esc(p_tok)} in / {_esc(c_tok)} out '
                f'({_esc(calls)} llamadas, ${cost_f:.4f} USD)</li>'
            )
        if rows:
            parts.append("<ul>" + "".join(rows) + "</ul>")
    return '<section class="cost-section"><h2>Costo estimado</h2>' + "".join(parts) + "</section>"


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def generar_reporte_html(
    batch_result: Optional[BatchResult],
    analisis: Optional[Dict[str, Any]] = None,
    diagnostic_text: Optional[str] = None,
    agent_name: str = "",
) -> str:
    """Construye un reporte HTML self-contained.

    Args:
        batch_result: resultado del batch del contra-agente.
        analisis: dict opcional con problemas/issues detectados.
        diagnostic_text: texto narrativo de diagnostico (ver `diagnostic.py`).
        agent_name: nombre legible del agente; si vacio usa agent_id.

    Returns:
        HTML completo como string.
    """
    nombre = agent_name or (
        getattr(batch_result, "agent_id", "") if batch_result is not None else ""
    ) or "Agente"
    pr = 0.0
    total = 0
    passed = 0
    if batch_result is not None:
        try:
            pr = float(getattr(batch_result, "pass_rate", 0.0) or 0.0)
        except (TypeError, ValueError):
            pr = 0.0
        total = int(getattr(batch_result, "total", 0) or 0)
        passed = int(getattr(batch_result, "passed", 0) or 0)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scorecard = getattr(batch_result, "scorecard", {}) if batch_result is not None else {}
    results = getattr(batch_result, "results", []) if batch_result is not None else []
    cost_summary = getattr(batch_result, "cost_summary", None) if batch_result is not None else None

    sections: List[str] = []
    sections.append(
        '<section class="score-global"><h2>Score global</h2>'
        + _bar(pr, label=f"{_pct(pr)} ({passed}/{total} aprobadas)")
        + "</section>"
    )
    sections.append(
        '<section class="dimensiones"><h2>Dimensiones</h2>'
        + _dim_cards(scorecard or {})
        + "</section>"
    )

    e2e_html = _e2e_section(results or [])
    if e2e_html:
        sections.append(e2e_html)

    problemas_html = _problemas_section(analisis)
    if problemas_html:
        sections.append(problemas_html)

    if diagnostic_text:
        sections.append(
            '<section class="diagnostico-wrap"><h2>Diagnostico narrativo</h2>'
            f'<div class="diagnostico">{_esc(diagnostic_text)}</div>'
            "</section>"
        )

    cost_html = _cost_section(cost_summary)
    if cost_html:
        sections.append(cost_html)

    header = (
        f'<header>{_esc(nombre)} &mdash; score {_esc(_pct(pr))}</header>'
    )
    footer = f"<footer>Generado por Lambda Analytics Juez &middot; {_esc(timestamp)}</footer>"

    return (
        "<!DOCTYPE html>"
        '<html lang="es"><head>'
        '<meta charset="utf-8">'
        f"<title>Juez &mdash; {_esc(nombre)}</title>"
        f"<style>{_css()}</style>"
        "</head><body>"
        + header
        + "".join(sections)
        + footer
        + "</body></html>"
    )


__all__ = ["generar_reporte_html"]
