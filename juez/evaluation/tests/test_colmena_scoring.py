"""Tests de score_project: agregacion de findings a un score/estado unico.

Cubre dos debilidades reales encontradas en auditoria:
  1. El tope de penalizacion por categoria era exactamente el peso nominal de
     la categoria (1x) -- un solo finding critical ya lo agotaba, asi que 1 vs
     40 findings critical en la misma categoria penalizaban IGUAL. Ahora el
     tope es un multiplo (4x) del peso, asi que acumular findings graves en
     una misma categoria SI pesa mas que uno solo.
  2. `business_rule` no tenia peso propio (caia al default de 5.0, igual que
     documentation) y un critical en esa categoria no bloqueaba el proyecto
     (solo lo hacia un critical de `security`). Ahora pesa igual que security
     (30.0) y tambien bloquea.
"""
from __future__ import annotations

from juez.colmena.models import NormalizedFinding
from juez.colmena.project_evaluator import score_project


def _finding(category: str, severity: str, n: int = 0) -> NormalizedFinding:
    return NormalizedFinding(
        id=f"{category}-{severity}-{n}",
        severity=severity,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        title="x",
        description="x",
        source="test",
    )


def test_multiples_criticos_en_la_misma_categoria_penalizan_mas_que_uno_solo():
    un_critico = score_project([_finding("workflow", "critical")])
    varios_criticos = score_project([_finding("workflow", "critical", i) for i in range(6)])

    assert varios_criticos.score < un_critico.score, (
        "6 findings critical en 'workflow' deberian penalizar mas que 1 solo "
        f"(un_critico={un_critico.score}, varios={varios_criticos.score})"
    )


def test_tope_de_categoria_sigue_acotado_no_borra_el_score_entero():
    """El tope (4x el peso) evita que una sola categoria, por spam de
    findings de bajo peso, tumbe el score a 0 -- sigue habiendo un limite."""
    resultado = score_project([_finding("documentation", "critical", i) for i in range(50)])
    # peso documentation=5.0, tope=5*4=20 -> score no puede bajar de 80.
    assert resultado.score >= 79.0


def test_business_rule_pesa_igual_que_security():
    security = score_project([_finding("security", "critical")])
    business_rule = score_project([_finding("business_rule", "critical")])
    assert business_rule.score == security.score


def test_critical_de_business_rule_bloquea_el_proyecto():
    resultado = score_project([_finding("business_rule", "critical")])
    assert resultado.status == "blocked_by_critical_findings"


def test_critical_de_security_sigue_bloqueando_el_proyecto():
    resultado = score_project([_finding("security", "critical")])
    assert resultado.status == "blocked_by_critical_findings"


def test_critical_de_categoria_no_bloqueante_no_bloquea():
    resultado = score_project([_finding("documentation", "critical")])
    assert resultado.status != "blocked_by_critical_findings"
    assert resultado.status == "not_ready_for_production"


def test_sin_findings_da_score_perfecto_y_listo():
    resultado = score_project([])
    assert resultado.score == 100.0
    assert resultado.status == "ready_for_production"
