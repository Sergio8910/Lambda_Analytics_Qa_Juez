from __future__ import annotations

from pathlib import Path
import os
import tempfile

from evaluation.api import server


def _base_report_dict() -> dict:
    return {
        "summary": {
            "run_id": "run-pretty-01",
            "total_cases": 2,
            "passed_cases": 1,
            "failed_cases": 1,
            "pass_rate": 0.5,
            "reliability_score": 0.95,
            "by_metric_failures": {"answer_relevancy": 1, "task_success": 1},
            "by_tag_failures": {"edge": 1, "autogen": 1},
            "recommendations": [
                "Responder todas las subpreguntas en orden.",
                "Evitar texto redundante.",
            ],
            "executive_summary": {
                "verdict": "NO CUMPLE",
                "risk_level": "MEDIO",
                "audit_mode": "balanced",
                "human_summary": "La corrida presenta fallos de relevancia y cobertura.",
                "recommended_actions": ["Ajustar el prompt base."],
            },
        },
        "cases": [
            {
                "case_id": "AUTO-001",
                "input": "Dame el precio de detergente 1L y jabon 1kg.",
                "tags": ["autogen", "edge"],
                "severity": "alta",
                "passed": False,
                "metrics": [
                    {
                        "name": "answer_relevancy",
                        "score": 0.2,
                        "success": False,
                        "reason_es": "No respondio una subpregunta.",
                    }
                ],
                "feedback": {
                    "overall": {
                        "primary_fail_reasons": [
                            "No cubre todas las subpreguntas solicitadas."
                        ]
                    }
                },
            }
        ],
        "spec": {
            "run_id": "run-pretty-01",
            "evaluation_profile": "balanced",
            "metrics": [
                {"name": "answer_relevancy", "enabled": True},
                {"name": "task_success", "enabled": True},
            ],
            "gating_metrics": ["task_success_deterministic", "unsupported_claims"],
        },
    }


def test_summary_txt_pretty_sections() -> None:
    report_dict = _base_report_dict()
    txt = server._build_pretty_summary_txt(report_dict, narrative_text="Narrativa de prueba.")
    assert "=== RESUMEN EJECUTIVO ===" in txt
    assert "=== TOP HALLAZGOS ===" in txt
    assert "=== FIX DE PROMPT VALIDADO ===" in txt
    assert "=== VEREDICTO FINAL ===" in txt


def test_summary_txt_includes_fix_demo_when_available() -> None:
    report_dict = _base_report_dict()
    report_dict["summary"]["prompt_fix_demo"] = {
        "cases_attempted": 1,
        "cases_improved": 1,
        "demonstrations": [
            {
                "case_id": "AUTO-001",
                "prompt_patch": {"rules": ["Regla 1"], "text": "Patch aplicado"},
                "improvements": [
                    {"metric": "answer_relevancy", "before_score": 0.2, "after_score": 0.8, "delta": 0.6}
                ],
                "example_prompt_fixed": "Prompt corregido de ejemplo.",
            }
        ],
    }
    txt = server._build_pretty_summary_txt(report_dict, narrative_text=None)
    block = txt.split("=== FIX DE PROMPT VALIDADO ===", 1)[1].split(
        "=== RECOMENDACIONES PRIORITARIAS ===", 1
    )[0]
    assert "Casos re-ejecutados: 1" in block
    assert "Caso: AUTO-001" in block
    assert "Prompt corregido (ejemplo):" in block


def test_summary_txt_no_fix_demo_block_when_absent() -> None:
    report_dict = _base_report_dict()
    txt = server._build_pretty_summary_txt(report_dict, narrative_text=None)
    block = txt.split("=== FIX DE PROMPT VALIDADO ===", 1)[1].split(
        "=== RECOMENDACIONES PRIORITARIAS ===", 1
    )[0]
    assert "Sin evidencia disponible." in block


def test_summary_txt_reason_forces_spanish() -> None:
    report_dict = _base_report_dict()
    report_dict["cases"][0]["metrics"][0]["reason_es"] = ""
    report_dict["cases"][0]["metrics"][0]["reason"] = (
        "The score is 0.00 because the response fails to address the request."
    )
    txt = server._build_pretty_summary_txt(report_dict, narrative_text=None)
    assert "The score is" not in txt
    assert "fails to address" not in txt


def test_outputs_two_files_only() -> None:
    report_dict = _base_report_dict()
    with tempfile.TemporaryDirectory() as tmp_dir:
        current = Path.cwd()
        os.chdir(tmp_dir)
        try:
            server._save_outputs(
                "two-files-check-01",
                report_dict,
                response={},
                summary_text="Resumen de prueba.",
            )
            out_dir = Path("outputs")
            files = sorted([f.name for f in out_dir.iterdir() if f.is_file()])
            assert files == ["two-files-check-01.json", "two-files-check-01_summary.txt"]
        finally:
            os.chdir(current)
