"""Indice de cobertura: el reporte declara que dimensiones se evaluaron y
cuales se omitieron (con motivo + como activarlas). Convierte el 'opt-in
silencioso' en una metrica visible -- el consumidor ya no confunde 'sin
hallazgos' con 'todo revisado'.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from juez.colmena.project_evaluator import evaluate_project_path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_proyecto_minimo_declara_dimensiones_omitidas():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "agente_prompt.txt", "Instrucciones del sistema: eres un agente de soporte.")
        cov = evaluate_project_path(root).coverage
        assert cov["completa"] is False
        # Seguridad/arquitectura corren siempre.
        assert cov["dimensiones"]["seguridad_estatica"]["estado"] == "evaluada"
        # Sin reglas explicitas -> omitida con instruccion de como activarla.
        rn = cov["dimensiones"]["reglas_negocio"]
        assert rn["estado"] == "omitida"
        assert "reglas_negocio.json" in rn["como_activar"]


def test_reglas_explicitas_marcan_dimension_evaluada():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "agente_prompt.txt", "Instrucciones del sistema del agente.")
        _write(root / "reglas_negocio.json", json.dumps({
            "reglas": [{"descripcion": "Nunca reembolsar mas de 100 USD sin aprobacion."}]
        }))
        cov = evaluate_project_path(root).coverage
        assert cov["dimensiones"]["reglas_negocio"]["estado"] == "evaluada"


def test_objetivos_no_aplica_sin_flujos_n8n():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "agente_prompt.txt", "Instrucciones del sistema del agente.")
        cov = evaluate_project_path(root).coverage
        assert cov["dimensiones"]["objetivos_flujos"]["estado"] == "no_aplica"


def test_archivo_grande_marca_cobertura_parcial():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "agente_prompt.txt", "Instrucciones del sistema del agente.")
        (root / "gigante.json").write_text("x" * 2_000_050, encoding="utf-8")
        cov = evaluate_project_path(root).coverage
        assert cov["dimensiones"]["cobertura_archivos"]["estado"] == "parcial"
        assert cov["completa"] is False


def test_resumen_cuenta_evaluadas_y_omitidas():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "agente_prompt.txt", "Instrucciones del sistema del agente.")
        resumen = evaluate_project_path(root).coverage["resumen"]
        assert resumen["evaluadas"] >= 1
        assert resumen["omitidas"] >= 1
        assert len(resumen["omitidas_detalle"]) == resumen["omitidas"] + resumen["parciales"]
