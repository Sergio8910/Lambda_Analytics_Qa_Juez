from __future__ import annotations

from evaluation.judge_engine import extract_claims, score_claims_against_context


def test_extract_claims_simple() -> None:
    text = "El precio es $2.00. Gracias. Disponemos de leche."
    claims = extract_claims(text)
    assert "El precio es $2.00" in claims
    assert "Disponemos de leche" in claims


def test_score_claims_against_context() -> None:
    claims = ["La leche cuesta $1.29", "Hay promociones secretas"]
    context = ["Lacteos: leche entera 1L $1.29, yogurt natural 500g $2.50."]
    analysis = score_claims_against_context(claims, context)
    assert analysis.supported_ratio >= 0.5
    assert analysis.unverifiable_ratio >= 0.0


def test_claims_numeros_con_evidencia() -> None:
    claims = ["La leche cuesta $1.29"]
    context = ["La leche cuesta $1.29."]
    analysis = score_claims_against_context(claims, context, penalize_numbers=True)
    assert analysis.claims[0].verdict == "supported"
    assert analysis.supported_ratio == 1.0


def test_claims_numeros_sin_contexto_penaliza() -> None:
    claims = ["La leche cuesta $1.29"]
    context: list[str] = []
    analysis = score_claims_against_context(claims, context, penalize_numbers=True)
    assert analysis.claims[0].verdict == "contradicted"


def test_claims_sin_numeros_sin_contexto() -> None:
    claims = ["La leche es saludable"]
    context: list[str] = []
    analysis = score_claims_against_context(claims, context, penalize_numbers=True)
    assert analysis.claims[0].verdict == "unverifiable"
