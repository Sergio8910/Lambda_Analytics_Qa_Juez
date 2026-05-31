"""Tests para la tabla side-by-side (esperado vs observado) del reporter.

Cubrimos:
  - Happy path: todas las métricas OK → todas las filas marcan [OK].
  - Falla: una métrica con observado < esperado → fila marcada como [FAIL].
  - Skip: verdict con status=skipped → renderer no produce líneas.
  - Legacy: verdict sin expected_snapshot → no crashea, retorna [].
"""
from __future__ import annotations

from juez.evaluation.contra_agente.reporter import _renderizar_sidebyside


# ── Helpers ──────────────────────────────────────────────────────────────────

def _verdict_completed_happy() -> dict:
    """Verdict happy: PDF tiene exactamente lo esperado."""
    return {
        "status": "completed",
        "verdict": "OK",
        "score": 1.0,
        "checks": [
            {
                "name": "integridad",
                "verdict": "OK",
                "score": 1.0,
                "metrics": {"paginas": 8},
            },
            {
                "name": "conteo_fotos_total",
                "verdict": "OK",
                "score": 1.0,
                "metrics": {"fotos_esperadas": 22, "fotos_embebidas": 22},
            },
            {
                "name": "ambientes_presentes",
                "verdict": "OK",
                "score": 1.0,
                "metrics": {
                    "ambientes_presentes": 2,
                    "ambientes_faltantes": [],
                    "ambientes_esperados": ["Cocina", "Bano social"],
                },
            },
            {
                "name": "fotos_por_ambiente",
                "verdict": "OK",
                "score": 1.0,
                "metrics": {
                    "fotos_por_ambiente": {
                        "Cocina":      {"esperado": 12, "observado": 12},
                        "Bano social": {"esperado": 10, "observado": 10},
                    },
                    "indeterminado": 0,
                    "total_esperado": 22,
                },
            },
            {
                "name": "campos_requeridos",
                "verdict": "OK",
                "score": 1.0,
                "metrics": {"campos_faltantes": []},
            },
        ],
        "issues": [],
    }


def _expected_snapshot_completo() -> dict:
    return {
        "artifact_id": "42",
        "counts": {"fotos": 22, "ambientes": 2},
        "structure": {
            "ambientes": ["Cocina", "Bano social"],
            "fotos_por_ambiente": {"Cocina": 12, "Bano social": 10},
            "tipo_inventario": "INICIAL",
        },
        "required_strings": ["1234"],
    }


# ── Happy path ───────────────────────────────────────────────────────────────
def test_happy_path_todas_filas_ok():
    verdict = _verdict_completed_happy()
    expected = _expected_snapshot_completo()
    lineas = _renderizar_sidebyside(verdict, expected)

    assert lineas, "Debe producir líneas cuando hay datos"
    contenido = "\n".join(lineas)

    # Header presente
    assert "Metrica" in contenido
    assert "Esperado" in contenido
    assert "Observado" in contenido
    assert "Estado" in contenido

    # Las filas esperadas aparecen
    assert "Fotos totales" in contenido
    assert "Ambientes" in contenido
    assert "Cocina (fotos)" in contenido
    assert "Bano social (fotos)" in contenido

    # Todas las filas deben tener [OK] — y ninguna [FAIL] ni [WARN]
    # (excepto headers que dicen "Estado" en columna)
    filas_datos = [
        l for l in lineas
        if l.strip() and not l.strip().startswith("-")
        and "Metrica" not in l and "Esperado" not in l
    ]
    assert len(filas_datos) >= 4, "Debe haber al menos 4 filas de datos"
    for fila in filas_datos:
        assert "[OK]" in fila, f"Fila no marca OK: {fila!r}"
        assert "[FAIL]" not in fila
        assert "[WARN]" not in fila


# ── Falla: una métrica observada < esperada ──────────────────────────────────
def test_metrica_faltante_marca_fail():
    """Si el PDF tiene 20 fotos pero se esperaban 22 → fila Fotos totales [FAIL]."""
    verdict = _verdict_completed_happy()
    # Ajustamos el check de conteo: observado < esperado
    for c in verdict["checks"]:
        if c["name"] == "conteo_fotos_total":
            c["metrics"] = {"fotos_esperadas": 22, "fotos_embebidas": 20}
            c["verdict"] = "WARN"
            c["score"] = 0.909
        if c["name"] == "fotos_por_ambiente":
            c["metrics"]["fotos_por_ambiente"]["Cocina"]["observado"] = 10

    lineas = _renderizar_sidebyside(verdict, _expected_snapshot_completo())
    contenido = "\n".join(lineas)

    # La fila "Fotos totales" debe mostrar 22 esperado, 20 observado, [FAIL]
    fila_fotos = next(l for l in lineas if "Fotos totales" in l)
    assert "22" in fila_fotos
    assert "20" in fila_fotos
    assert "[FAIL]" in fila_fotos

    # Cocina también debe mostrar fail (esperado 12, observado 10)
    fila_cocina = next(l for l in lineas if "Cocina (fotos)" in l)
    assert "[FAIL]" in fila_cocina


def test_observado_mayor_que_esperado_es_warn():
    """Si el PDF embebió 23 fotos pero se esperaban 22 → [WARN] (no [FAIL])."""
    verdict = _verdict_completed_happy()
    for c in verdict["checks"]:
        if c["name"] == "conteo_fotos_total":
            c["metrics"] = {"fotos_esperadas": 22, "fotos_embebidas": 23}

    lineas = _renderizar_sidebyside(verdict, _expected_snapshot_completo())
    fila_fotos = next(l for l in lineas if "Fotos totales" in l)
    assert "[WARN]" in fila_fotos
    assert "[FAIL]" not in fila_fotos


# ── Skipped ──────────────────────────────────────────────────────────────────
def test_verdict_skipped_no_renderiza():
    """Verdict skipped → renderer retorna lista vacía."""
    verdict = {
        "status": "skipped",
        "skip_reason": "verificador_unavailable",
        "artifact_id": "JUEZ-E2E-X-01",
    }
    lineas = _renderizar_sidebyside(verdict, _expected_snapshot_completo())
    assert lineas == []


def test_verdict_none_no_renderiza():
    """Verdict None → lista vacía sin crash."""
    assert _renderizar_sidebyside(None, _expected_snapshot_completo()) == []


# ── Legacy: sin expected_snapshot ────────────────────────────────────────────
def test_sin_expected_snapshot_no_crashea_y_retorna_vacio():
    """Verdict legacy sin snapshot → retorna [] sin crash."""
    verdict = _verdict_completed_happy()
    assert _renderizar_sidebyside(verdict, None) == []
    assert _renderizar_sidebyside(verdict, {}) == []


def test_expected_snapshot_vacio_no_genera_filas():
    """Snapshot con counts/structure vacíos → retorna []."""
    verdict = _verdict_completed_happy()
    snap_vacio = {"counts": {}, "structure": {}, "required_strings": []}
    assert _renderizar_sidebyside(verdict, snap_vacio) == []


# ── Campo requerido presente ─────────────────────────────────────────────────
def test_campo_requerido_ausente_marca_fail():
    """Si el campo está en campos_faltantes → fila 'Si vs No [FAIL]'."""
    verdict = _verdict_completed_happy()
    for c in verdict["checks"]:
        if c["name"] == "campos_requeridos":
            c["metrics"] = {"campos_faltantes": ["1234"]}
            c["verdict"] = "FAIL"
            c["score"] = 0.0

    expected = _expected_snapshot_completo()
    lineas = _renderizar_sidebyside(verdict, expected)

    # Debe haber una fila para "1234 presente"
    fila_campo = next((l for l in lineas if "presente" in l), None)
    assert fila_campo is not None
    assert "Si" in fila_campo
    assert "No" in fila_campo
    assert "[FAIL]" in fila_campo


# ── Verdict sin checks (defensive) ───────────────────────────────────────────
def test_verdict_sin_checks_todavia_renderiza_esperados():
    """Si verdict no tiene checks, los esperados se ven pero observados = ? → n/a."""
    verdict = {"status": "completed", "verdict": "OK", "score": 1.0, "checks": []}
    lineas = _renderizar_sidebyside(verdict, _expected_snapshot_completo())
    # Debe haber filas (al menos para Fotos totales / Ambientes / por-ambiente)
    assert lineas
    contenido = "\n".join(lineas)
    assert "Fotos totales" in contenido
    # Observado vacío → "?" y estado [n/a]
    assert "[n/a]" in contenido
