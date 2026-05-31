"""Tests unitarios de MockToolRunner — heurísticas por nombre de tool."""
from __future__ import annotations

import pytest

from juez.evaluation.contra_agente.synthetic.mock_tools import MockToolRunner


def _canonical():
    """Datos canónicos minimal para alimentar al MockToolRunner."""
    return {
        "source": "synthetic",
        "contrato_id": "JUEZ-E2E-ABCDEF-01",
        "inventario_id": 99123,
        "propietario": "Propietario Sintético",
        "arrendatario": "Arrendatario Sintético",
        "tipo_inventario": "INICIAL",
        "ambientes": ["Cocina", "Sala", "Habitación principal"],
        "fotos_por_ambiente": {"Cocina": 5, "Sala": 7, "Habitación principal": 9},
        "total_fotos": 21,
    }


# ── self.calls log ───────────────────────────────────────────────────────────

def test_run_appends_to_calls_log():
    runner = MockToolRunner(_canonical())
    assert runner.calls == []

    runner.run("registrar_inmueble", {"foo": "bar"})
    runner.run("generar_pdf", {})

    assert len(runner.calls) == 2
    assert runner.calls[0]["tool"] == "registrar_inmueble"
    assert runner.calls[0]["args"] == {"foo": "bar"}
    assert runner.calls[1]["tool"] == "generar_pdf"
    assert runner.calls[1]["args"] == {}


def test_run_handles_none_args_gracefully():
    """args=None no debe romper — se debe normalizar a dict vacío en el log."""
    runner = MockToolRunner(_canonical())
    result = runner.run("generar_pdf", None)  # type: ignore[arg-type]
    assert result["success"] is True
    # En el log, args debería ser un dict (vacío)
    assert runner.calls[0]["args"] == {}


# ── Heurística PDF ───────────────────────────────────────────────────────────

def test_pdf_heuristic_returns_pdf_payload():
    runner = MockToolRunner(_canonical())
    result = runner.run("generar_pdf", {})
    assert result["success"] is True
    assert "pdf_drive_file_id" in result
    assert result["pdf_drive_file_id"].startswith("synth_pdf_")
    assert "pdf_url" in result
    # canonical_data se reusa: contrato_id sale en la URL
    assert "JUEZ-E2E-ABCDEF-01" in result["pdf_url"]


def test_pdf_heuristic_matches_any_tool_with_pdf_in_name():
    runner = MockToolRunner(_canonical())
    result = runner.run("subir_pdf_drive", {})
    assert result["success"] is True
    assert "pdf_url" in result
    assert "pdf_drive_file_id" in result


# ── Heurística registrar_inmueble / sesion / iniciar ─────────────────────────

def test_registrar_inmueble_heuristic():
    canonical = _canonical()
    runner = MockToolRunner(canonical)
    result = runner.run("registrar_inmueble", {"contrato": canonical["contrato_id"]})
    assert result["success"] is True
    assert result["inmueble_id"] == 1001
    assert result["inventario_id"] == canonical["inventario_id"]
    assert result["tipo_inventario"] == "INICIAL"


def test_sesion_heuristic():
    runner = MockToolRunner(_canonical())
    result = runner.run("crear_sesion", {})
    assert result["success"] is True
    assert "inmueble_id" in result
    assert "inventario_id" in result


def test_iniciar_heuristic():
    runner = MockToolRunner(_canonical())
    result = runner.run("iniciar_proceso", {})
    assert result["success"] is True
    assert "inmueble_id" in result
    assert "inventario_id" in result


# ── Heurística ambiente + (registrar|crear|guardar) ──────────────────────────

def test_registrar_ambiente_heuristic():
    canonical = _canonical()
    runner = MockToolRunner(canonical)
    result = runner.run("registrar_ambiente", {"ambiente": "Cocina"})
    assert result["success"] is True
    assert "ambiente_id" in result
    assert result["ambiente"] == "Cocina"
    # esperadas_fotos del canonical
    assert result["esperadas_fotos"] == canonical["fotos_por_ambiente"]["Cocina"]


def test_crear_ambiente_heuristic():
    runner = MockToolRunner(_canonical())
    result = runner.run("crear_ambiente", {"nombre": "Sala"})
    assert result["success"] is True
    assert result["ambiente"] == "Sala"
    assert result["esperadas_fotos"] == 7


def test_guardar_ambiente_heuristic():
    runner = MockToolRunner(_canonical())
    result = runner.run("guardar_ambiente", {"room_name": "Habitación principal"})
    assert result["success"] is True
    assert result["ambiente"] == "Habitación principal"
    assert result["esperadas_fotos"] == 9


def test_ambiente_ids_increment_monotonically():
    runner = MockToolRunner(_canonical())
    r1 = runner.run("registrar_ambiente", {"ambiente": "Cocina"})
    r2 = runner.run("registrar_ambiente", {"ambiente": "Sala"})
    assert r2["ambiente_id"] > r1["ambiente_id"]


def test_ambiente_without_create_verb_falls_through():
    """`obtener_ambiente` matchea 'obtener', no la heurística de ambiente."""
    runner = MockToolRunner(_canonical())
    result = runner.run("obtener_ambiente", {})
    assert result["success"] is True
    # Esta llamada debería matchear consulta/obtener (rows), no ambiente_id
    assert "rows" in result or "ambiente_id" not in result


# ── Heurística cerrar / finalizar ────────────────────────────────────────────

def test_cerrar_heuristic():
    canonical = _canonical()
    runner = MockToolRunner(canonical)
    result = runner.run("cerrar_inventario", {})
    assert result["success"] is True
    assert result["inventario_id"] == canonical["inventario_id"]
    # ambientes count
    assert result["ambientes"] == len(canonical["ambientes"])
    assert result["total_items"] == sum(canonical["fotos_por_ambiente"].values())


def test_finalizar_heuristic():
    canonical = _canonical()
    runner = MockToolRunner(canonical)
    result = runner.run("finalizar_proceso", {})
    assert result["success"] is True
    assert result["ambientes"] == len(canonical["ambientes"])


# ── Tool desconocida ─────────────────────────────────────────────────────────

def test_unknown_tool_returns_synthetic_response():
    runner = MockToolRunner(_canonical())
    result = runner.run("random_unknown_tool", {"x": 1})
    assert result["success"] is True
    assert result["info"] == "synthetic_response"
    assert result["tool_name"] == "random_unknown_tool"


# ── canonical_data se reusa ──────────────────────────────────────────────────

def test_canonical_contrato_id_in_pdf_response():
    canonical = _canonical()
    canonical["contrato_id"] = "MI-CONTRATO-XYZ"
    runner = MockToolRunner(canonical)
    result = runner.run("generar_pdf", {})
    assert "MI-CONTRATO-XYZ" in result["pdf_url"]


def test_canonical_inventario_id_in_registrar_response():
    canonical = _canonical()
    canonical["inventario_id"] = 88888
    runner = MockToolRunner(canonical)
    result = runner.run("registrar_inmueble", {})
    assert result["inventario_id"] == 88888
