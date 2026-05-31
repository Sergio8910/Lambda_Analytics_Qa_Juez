"""Tests unitarios de `_attach_e2e_expectations` en generator.

NO toca OpenAI, NO toca HTTP, NO toca BD real. Mockea `snapshot_factory`
para forzar el path sintético (incluso con `real_inventario_id`) y verifica
que los planes queden marcados como corresponde.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from juez.evaluation.contra_agente import generator
from juez.evaluation.contra_agente.generator import _attach_e2e_expectations
from juez.evaluation.contra_agente.models import (
    ConversationPlan,
    Persona,
    TurnSpec,
)
from juez.evaluation.contra_agente.synthetic import snapshot_factory


# ── Helpers ──────────────────────────────────────────────────────────────────
def _make_plan(plan_id: str, category: str = "happy_path") -> ConversationPlan:
    """Construye un ConversationPlan minimal y valido."""
    return ConversationPlan(
        plan_id=plan_id,
        category=category,
        severity="media",
        tags=[category],
        success_threshold=0.70,
        max_turns=2,
        persona=Persona(
            name="Usuario Test",
            mood="cordial",
            backstory="Persona de test",
            language_style="informal",
        ),
        turns=[
            TurnSpec(
                turn_id=1,
                turn_type="opener",
                intent="saludar",
                message_template="Hola, buenas tardes.",
                success_criteria="El agente saluda",
                metrics=["task_success"],
            ),
            TurnSpec(
                turn_id=2,
                turn_type="probe",
                intent="consultar",
                message_template="Necesito info.",
                success_criteria="El agente responde",
                metrics=["task_success"],
            ),
        ],
    )


def _fake_synthetic_data(
    batch_id: str,
    plan_idx: int = 1,
    real_inventario_id: int | None = None,
    source: str = "synthetic",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Retorna un (expected_snapshot, canonical_data) deterministico
    que respeta el contrato esperado por `_attach_e2e_expectations`."""
    artifact_id = f"JUEZ-E2E-FAKE-{plan_idx:02d}"
    expected_snapshot = {
        "artifact_id": artifact_id,
        "counts": {"fotos": 10, "ambientes": 2},
        "structure": {
            "ambientes": ["Sala", "Cocina"],
            "fotos_por_ambiente": {"Sala": 5, "Cocina": 5},
            "tipo_inventario": "INICIAL",
        },
        "required_strings": [artifact_id, "Propietario X", "INICIAL"],
    }
    canonical_data = {
        "source": source,
        "contrato_id": artifact_id,
        "inventario_id": 99000 + plan_idx,
        "propietario": "Propietario X",
        "arrendatario": "Arrendatario X",
        "tipo_inventario": "INICIAL",
        "ambientes": ["Sala", "Cocina"],
        "fotos_por_ambiente": {"Sala": 5, "Cocina": 5},
        "total_fotos": 10,
    }
    return expected_snapshot, canonical_data


@pytest.fixture
def patch_synthetic(monkeypatch):
    """Monkeypatch del snapshot_factory.make_synthetic_data para no depender
    del random real. Devuelve un dispatcher controlado."""

    def _make_synthetic_data(batch_id, plan_idx=1):
        return _fake_synthetic_data(batch_id, plan_idx, source="synthetic")

    monkeypatch.setattr(
        snapshot_factory, "make_synthetic_data", _make_synthetic_data
    )
    # generator.py importa `_make_e2e_data` desde `snapshot_factory.make_data`.
    # Reescribimos make_data para que vaya al fake (manteniendo dispatcher).
    def _make_data(batch_id, plan_idx=1, real_inventario_id=None):
        if real_inventario_id is None:
            return _make_synthetic_data(batch_id, plan_idx)
        # cuando viene real_inventario_id, simulamos un fallback transparente
        # llamando real_db_source que pueda fallar (segun el test setup).
        try:
            from juez.evaluation.contra_agente.synthetic import real_db_source
            return real_db_source.make_real_db_data(real_inventario_id)
        except Exception:
            return _make_synthetic_data(batch_id, plan_idx)

    monkeypatch.setattr(generator, "_make_e2e_data", _make_data)
    return _make_data


# ── Tests ────────────────────────────────────────────────────────────────────
def test_e2e_k_zero_no_op(patch_synthetic):
    """e2e_k=0 NO debe tocar ningun plan."""
    plans = [_make_plan("conv_01"), _make_plan("conv_02")]
    _attach_e2e_expectations(plans, "batch_test_zero", e2e_k=0)
    for p in plans:
        assert p.artifact_expectation is None
        assert "e2e_artifact" not in p.tags


def test_e2e_k_one_marks_exactly_one(patch_synthetic):
    """e2e_k=1 marca exactamente 1 plan."""
    plans = [_make_plan(f"conv_{i:02d}") for i in range(1, 4)]
    _attach_e2e_expectations(plans, "batch_test_one", e2e_k=1)
    marked = [p for p in plans if p.artifact_expectation is not None]
    assert len(marked) == 1


def test_prefers_happy_path(patch_synthetic):
    """Cuando hay planes de varias categorias, el marcado debe ser happy_path."""
    plans = [
        _make_plan("conv_01", category="caos"),
        _make_plan("conv_02", category="agresivo"),
        _make_plan("conv_03", category="happy_path"),
        _make_plan("conv_04", category="happy_path"),
    ]
    _attach_e2e_expectations(plans, "batch_test_pref", e2e_k=1)
    marked = [p for p in plans if p.artifact_expectation is not None]
    assert len(marked) == 1
    assert marked[0].category == "happy_path"


def test_marked_plan_has_e2e_artifact_tag(patch_synthetic):
    """El plan marcado tiene tag 'e2e_artifact'."""
    plans = [_make_plan("conv_01")]
    _attach_e2e_expectations(plans, "batch_test_tag", e2e_k=1)
    assert "e2e_artifact" in plans[0].tags


def test_marked_plan_has_synthetic_source_tag(patch_synthetic):
    """Sin real_inventario_id, el plan marcado tiene tag 'e2e_source:synthetic'."""
    plans = [_make_plan("conv_01")]
    _attach_e2e_expectations(plans, "batch_test_src", e2e_k=1, real_inventario_id=None)
    assert "e2e_source:synthetic" in plans[0].tags


def test_first_turn_message_enriched_with_artifact_id(patch_synthetic):
    """El primer turno del plan marcado tiene message_template enriquecido."""
    plans = [_make_plan("conv_01")]
    original_msg = plans[0].turns[0].message_template
    _attach_e2e_expectations(plans, "batch_test_msg", e2e_k=1)
    first = plans[0].turns[0]
    assert original_msg in first.message_template
    assert "referencia de inventario" in first.message_template
    # Debe contener el artifact_id (que es JUEZ-E2E-FAKE-XX por el fake)
    assert "JUEZ-E2E-FAKE" in first.message_template


def test_first_turn_variables_has_artifact_id(patch_synthetic):
    """first.variables['artifact_id'] debe estar poblado."""
    plans = [_make_plan("conv_01")]
    _attach_e2e_expectations(plans, "batch_test_var", e2e_k=1)
    first = plans[0].turns[0]
    assert "artifact_id" in first.variables
    assert first.variables["artifact_id"].startswith("JUEZ-E2E-FAKE")


def test_real_id_with_failing_db_falls_back_to_synthetic(monkeypatch):
    """Con real_inventario_id pero BD mockeada para fallar -> fallback
    transparente a sintetico (no crash, tag queda como synthetic)."""

    # 1) Reescribimos make_synthetic_data para que sea deterministico.
    def _fake_synth(batch_id, plan_idx=1):
        return _fake_synthetic_data(batch_id, plan_idx, source="synthetic")

    monkeypatch.setattr(snapshot_factory, "make_synthetic_data", _fake_synth)

    # 2) Forzamos que real_db_source.make_real_db_data lance error.
    #    Como `make_data` ya tiene try/except que cae a sintetico,
    #    no debe crashear.
    def _fake_real_db(real_inventario_id: int):
        raise RuntimeError("BD productiva caida (mock)")

    # snapshot_factory hace `from .real_db_source import make_real_db_data`
    # adentro del try. Patcheamos a nivel del modulo real_db_source.
    import juez.evaluation.contra_agente.synthetic.real_db_source as rds_mod
    monkeypatch.setattr(rds_mod, "make_real_db_data", _fake_real_db, raising=False)

    plans = [_make_plan("conv_01")]
    # No debe crashear; el dispatcher real (`make_data`) cae a sintetico.
    _attach_e2e_expectations(
        plans, "batch_test_fallback", e2e_k=1, real_inventario_id=12345
    )

    assert plans[0].artifact_expectation is not None
    # El tag de source debe quedar como synthetic (fallback transparente).
    assert "e2e_source:synthetic" in plans[0].tags
    # No debe haber tag de source real.
    assert not any(
        t.startswith("e2e_source:") and "synthetic" not in t for t in plans[0].tags
    )
