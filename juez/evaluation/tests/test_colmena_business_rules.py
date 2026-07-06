from __future__ import annotations

import json
import tempfile
from pathlib import Path

from juez.colmena.business_rules import (
    BusinessRulesReport,
    business_rules_worker_findings,
    extract_business_rules,
    verify_functional_against_rules,
)
from juez.colmena.models import SyntheticTestResult
from juez.colmena.scanner import scan_project


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_explicit_rules_always_alta_confianza() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "reglas_negocio.json", json.dumps({
            "reglas": [
                {"descripcion": "El precio se calcula por tabla, nunca por inferencia.",
                 "componente_relacionado": "consulta-precios"},
            ]
        }))
        inventory = scan_project(root)
        report = extract_business_rules(root, inventory)

        alta = report.alta_confianza()
        assert len(alta) == 1
        assert alta[0].origen == "explicito"
        assert alta[0].confianza == "alta"


def test_inferred_doc_rules_never_alta() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "README.md", "El descuento maximo permitido es 20%.\nEsto es una linea normal.\n")
        inventory = scan_project(root)
        report = extract_business_rules(root, inventory)

        assert report.alta_confianza() == []
        inferidas = [r for r in report.reglas if r.origen == "inferido_doc"]
        assert inferidas
        assert all(r.confianza == "media" for r in inferidas)


def test_inferred_rules_do_not_gate_alone() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root / "README.md", "El descuento maximo permitido es 20%.\n")
        inventory = scan_project(root)
        findings, report = business_rules_worker_findings(root, inventory)

        # Solo hay un finding informativo (info), nunca critical/high por reglas inferidas.
        assert all(f.severity == "info" for f in findings)


def test_no_explicit_rules_means_no_findings() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inventory = scan_project(root)
        findings, report = business_rules_worker_findings(root, inventory)
        assert findings == []
        assert report.reglas == []


def test_verify_functional_detects_violation_of_explicit_rule() -> None:
    report = BusinessRulesReport(reglas=[{
        "id": "RN-001",
        "descripcion": "El precio se calcula por tabla de precios, nunca por inferencia.",
        "origen": "explicito",
        "confianza": "alta",
        "componente_relacionado": None,
    }])
    resultados = [
        SyntheticTestResult(
            case_id="caso-precio-01",
            passed=False,
            score=10,
            message="El agente calculo el precio por inferencia en vez de usar la tabla de precios.",
            findings=[{"category": "prompt", "severity": "high", "message": "precio inferido, no de tabla"}],
        )
    ]
    findings = verify_functional_against_rules(report, resultados)
    assert findings
    assert findings[0].severity == "critical"
    assert findings[0].category == "business_rule"


def test_verify_functional_no_violation_when_case_passes() -> None:
    report = BusinessRulesReport(reglas=[{
        "id": "RN-001",
        "descripcion": "El precio se calcula por tabla de precios, nunca por inferencia.",
        "origen": "explicito",
        "confianza": "alta",
    }])
    resultados = [SyntheticTestResult(case_id="ok", passed=True, score=100, message="todo bien")]
    assert verify_functional_against_rules(report, resultados) == []
