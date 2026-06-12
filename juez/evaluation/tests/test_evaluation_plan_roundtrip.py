"""Round-trip del flujo de 3 APIs del Juez:

  1. /v1/evaluation-plan  -> trae las reglas (con `umbral`)
  2. el usuario EDITA el umbral de una regla
  3. /v1/evaluate         -> debe aceptar esas reglas editadas y aplicar el umbral

Verifica el normalizador que cierra el empate de formatos entre ambos endpoints.
"""
from __future__ import annotations

from juez.evaluation.api.app import _normalize_metrics
from juez.evaluation.report_models import EvaluationSpec


def _reglas_formato_plan():
    """Como salen de /v1/evaluation-plan."""
    return [
        {
            "name": "instruction_adherence",
            "tipo": "llm",
            "umbral": 0.85,
            "requiere_contexto": False,
            "requiere_salida_esperada": True,
            "existe": True,
        },
        {
            "name": "format_compliance",
            "tipo": "deterministic",
            "umbral": 1.0,
            "requiere_contexto": False,
            "requiere_salida_esperada": False,
            "existe": True,
        },
    ]


def test_normaliza_formato_plan_a_metricspec():
    reglas = _reglas_formato_plan()
    norm = _normalize_metrics(reglas)
    # umbral -> threshold; se eliminan los campos descriptivos
    assert norm[0] == {"name": "instruction_adherence", "threshold": 0.85}
    assert norm[1] == {"name": "format_compliance", "threshold": 1.0}


def test_evaluationspec_acepta_reglas_normalizadas():
    """El motor (EvaluationSpec) tiene extra=forbid: sin normalizar, fallaría."""
    norm = _normalize_metrics(_reglas_formato_plan())
    spec = EvaluationSpec(run_id="t", metrics=norm)
    assert len(spec.metrics) == 2
    assert spec.metrics[0].name == "instruction_adherence"


def test_umbral_editado_por_el_usuario_se_aplica():
    """El corazón de la pregunta: si el usuario edita el umbral, se evalúa con ese."""
    reglas = _reglas_formato_plan()
    # El usuario sube la exigencia de 0.85 a 0.99
    reglas[0]["umbral"] = 0.99
    norm = _normalize_metrics(reglas)
    spec = EvaluationSpec(run_id="t", metrics=norm)
    metrica = next(m for m in spec.metrics if m.name == "instruction_adherence")
    assert metrica.threshold == 0.99  # se aplicó el umbral EDITADO, no el original


def test_acepta_tambien_formato_metricspec_nativo():
    nativo = [{"name": "answer_relevancy", "threshold": 0.7, "enabled": True}]
    norm = _normalize_metrics(nativo)
    assert norm[0]["name"] == "answer_relevancy"
    assert norm[0]["threshold"] == 0.7
    assert norm[0]["enabled"] is True


def test_metrica_marcada_inexistente_se_ignora():
    reglas = [
        {"name": "answer_relevancy", "umbral": 0.8, "existe": True},
        {"name": "no_existe_xyz", "existe": False, "nota": "desconocida"},
    ]
    norm = _normalize_metrics(reglas)
    assert len(norm) == 1
    assert norm[0]["name"] == "answer_relevancy"


def test_umbral_faltante_usa_default_del_catalogo():
    # Si la regla no trae umbral, se toma el default del catálogo METRICS.
    norm = _normalize_metrics([{"name": "instruction_adherence"}])
    assert norm[0]["threshold"] == 0.85  # default de instruction_adherence
