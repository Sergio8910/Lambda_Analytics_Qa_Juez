"""Tests unitarios para snapshot_factory.

Cubren:
  - Determinismo: mismo (batch_id, idx) -> snapshot idéntico.
  - Variabilidad: distintos idx -> snapshots distintos.
  - Schema esperado del expected_snapshot y canonical_data.
  - Coherencia: total fotos == sum(fotos_por_ambiente.values()).
  - Dispatcher make_data: sin real_id usa sintético; con real_id mockeado a
    falla cae a sintético sin levantar.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from juez.evaluation.contra_agente.synthetic.snapshot_factory import (
    make_data,
    make_synthetic_data,
)


# ── Determinismo / variabilidad ──────────────────────────────────────────────

def test_make_synthetic_data_es_deterministico():
    snap_a, can_a = make_synthetic_data("batch-xyz", 1)
    snap_b, can_b = make_synthetic_data("batch-xyz", 1)
    assert snap_a == snap_b
    assert can_a == can_b


def test_make_synthetic_data_varia_por_idx():
    snap1, can1 = make_synthetic_data("batch-xyz", 1)
    snap2, can2 = make_synthetic_data("batch-xyz", 2)
    # Al menos uno de los campos clave debe variar.
    assert (snap1 != snap2) or (can1 != can2)
    # contrato_id incluye el idx -> seguro distinto.
    assert can1["contrato_id"] != can2["contrato_id"]


def test_make_synthetic_data_varia_por_batch_id():
    snap1, can1 = make_synthetic_data("batch-aaa", 1)
    snap2, can2 = make_synthetic_data("batch-bbb", 1)
    assert can1["contrato_id"] != can2["contrato_id"]


# ── Schema del expected_snapshot ─────────────────────────────────────────────

def test_expected_snapshot_schema_basico():
    snap, _ = make_synthetic_data("batch-test", 1)
    # Top-level keys
    for key in ("artifact_id", "counts", "structure", "required_strings"):
        assert key in snap, f"falta key {key} en expected_snapshot"

    # counts
    assert isinstance(snap["counts"], dict)
    assert "fotos" in snap["counts"]
    assert "ambientes" in snap["counts"]
    assert isinstance(snap["counts"]["fotos"], int)
    assert isinstance(snap["counts"]["ambientes"], int)

    # structure
    structure = snap["structure"]
    assert isinstance(structure, dict)
    assert isinstance(structure["ambientes"], list)
    assert isinstance(structure["fotos_por_ambiente"], dict)
    assert "tipo_inventario" in structure

    # required_strings
    assert isinstance(snap["required_strings"], list)
    assert len(snap["required_strings"]) > 0


def test_canonical_source_synthetic():
    _, canonical = make_synthetic_data("batch-test", 1)
    assert canonical["source"] == "synthetic"


def test_canonical_tiene_campos_minimos():
    _, canonical = make_synthetic_data("batch-test", 1)
    for key in (
        "source", "contrato_id", "inventario_id", "propietario",
        "arrendatario", "tipo_inventario", "ambientes",
        "fotos_por_ambiente", "total_fotos",
    ):
        assert key in canonical, f"falta {key} en canonical_data"


# ── Coherencia interna ───────────────────────────────────────────────────────

def test_total_fotos_es_suma_de_fotos_por_ambiente():
    snap, canonical = make_synthetic_data("batch-suma", 1)
    suma_canonical = sum(canonical["fotos_por_ambiente"].values())
    assert canonical["total_fotos"] == suma_canonical
    assert snap["counts"]["fotos"] == suma_canonical


def test_count_ambientes_coincide_con_lista():
    snap, canonical = make_synthetic_data("batch-amb", 1)
    assert snap["counts"]["ambientes"] == len(snap["structure"]["ambientes"])
    assert len(canonical["ambientes"]) == snap["counts"]["ambientes"]


def test_fotos_por_ambiente_keys_son_lista_ambientes():
    _, canonical = make_synthetic_data("batch-keys", 1)
    assert set(canonical["fotos_por_ambiente"].keys()) == set(canonical["ambientes"])


def test_required_strings_incluye_contrato_y_propietario():
    snap, canonical = make_synthetic_data("batch-req", 1)
    req = snap["required_strings"]
    assert canonical["contrato_id"] in req
    assert canonical["propietario"] in req
    assert canonical["tipo_inventario"] in req


# ── Dispatcher make_data ─────────────────────────────────────────────────────

def test_make_data_sin_real_id_usa_sintetico():
    snap, canonical = make_data("batch-disp", 1, real_inventario_id=None)
    assert canonical["source"] == "synthetic"
    # Es determinístico igual que make_synthetic_data directo.
    snap_ref, canonical_ref = make_synthetic_data("batch-disp", 1)
    assert snap == snap_ref
    assert canonical == canonical_ref


def test_make_data_con_real_id_cae_a_sintetico_si_falla():
    """Si real_db_source falla, make_data NO debe levantar — usa sintético."""
    # ABAT_DB_URL vacío => _connect levanta RealDbError => fallback.
    with patch.dict("os.environ", {"ABAT_DB_URL": ""}, clear=False):
        # Aseguramos que no quede setteado en otro env (mejor explícito).
        import os
        os.environ.pop("ABAT_DB_URL", None)
        snap, canonical = make_data("batch-real-fail", 1, real_inventario_id=12345)
        # cayó a sintético => source synthetic
        assert canonical["source"] == "synthetic"
        # y matchea el sintético determinístico del mismo (batch_id, idx)
        snap_ref, _ = make_synthetic_data("batch-real-fail", 1)
        assert snap == snap_ref


def test_make_data_con_real_id_falla_explicita_mock():
    """Mockeamos make_real_db_data para que levante; debe caer a sintético."""
    from juez.evaluation.contra_agente.synthetic import snapshot_factory as sf

    def _boom(_inv_id):
        raise sf.__dict__.get("RealDbError", Exception)("simulated failure")

    # Importamos el módulo real_db_source y parcheamos su make_real_db_data.
    with patch(
        "juez.evaluation.contra_agente.synthetic.real_db_source.make_real_db_data",
        side_effect=RuntimeError("mock boom"),
    ):
        snap, canonical = make_data("batch-mock-fail", 1, real_inventario_id=999)
        assert canonical["source"] == "synthetic"


# ── Sanidad sobre rangos ─────────────────────────────────────────────────────

@pytest.mark.parametrize("idx", [1, 2, 3, 5, 10, 42])
def test_rangos_razonables(idx):
    snap, canonical = make_synthetic_data("batch-rng", idx)
    n_amb = snap["counts"]["ambientes"]
    assert 3 <= n_amb <= 5, f"n_ambientes fuera de rango: {n_amb}"
    for amb, n in canonical["fotos_por_ambiente"].items():
        assert 4 <= n <= 12, f"fotos de {amb} fuera de rango: {n}"
