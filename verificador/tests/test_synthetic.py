"""Tests del source `inline` y del cliente `abad_synthetic` para modo e2e sintético."""
from __future__ import annotations

import base64

import pytest

from verificador.clientes.abad_synthetic import AbadSyntheticClient
from verificador.clientes.base import ClientError, ClientNotFoundError
from verificador.schemas import ExpectedSnapshot
from verificador.sources.base import SourceError
from verificador.sources.inline import InlineSource


# ─────────────────────────────────────────────────────────────────────────────
# InlineSource
# ─────────────────────────────────────────────────────────────────────────────

def test_inline_decodifica_base64_correctamente():
    payload = b"%PDF-1.4\nfake content"
    spec = {"blob_base64": base64.b64encode(payload).decode("ascii")}
    out = InlineSource().fetch(spec)
    assert out == payload


def test_inline_falta_blob_base64():
    with pytest.raises(SourceError):
        InlineSource().fetch({})


def test_inline_blob_base64_invalido():
    with pytest.raises(SourceError):
        InlineSource().fetch({"blob_base64": "no-es-base64-valido!!!"})


def test_inline_blob_vacio():
    spec = {"blob_base64": base64.b64encode(b"").decode("ascii")}
    with pytest.raises(SourceError):
        InlineSource().fetch(spec)


def test_inline_registrada_en_registry():
    from verificador.sources import get_source
    src = get_source("inline")
    assert src.type_name == "inline"


# ─────────────────────────────────────────────────────────────────────────────
# AbadSyntheticClient
# ─────────────────────────────────────────────────────────────────────────────

_SAMPLE_SNAPSHOT = {
    "artifact_id": "JUEZ-E2E-AAA-01",
    "counts": {"fotos": 47, "ambientes": 6},
    "structure": {
        "ambientes": ["Cocina", "Sala", "Baño", "Hall", "Habitación 1", "Patio"],
        "fotos_por_ambiente": {
            "Cocina": 10, "Sala": 8, "Baño": 5, "Hall": 6, "Habitación 1": 12, "Patio": 6,
        },
        "tipo_inventario": "INICIAL",
    },
    "required_strings": ["JUEZ-E2E-AAA-01", "Propietario Sintético", "INICIAL"],
}


def test_abad_synthetic_lee_snapshot_desde_metadata():
    c = AbadSyntheticClient()
    out = c.fetch_expected(
        "JUEZ-E2E-AAA-01",
        request_metadata={"expected_snapshot": _SAMPLE_SNAPSHOT},
    )
    assert isinstance(out, ExpectedSnapshot)
    assert out.counts["fotos"] == 47
    assert out.structure["ambientes"][0] == "Cocina"


def test_abad_synthetic_sin_metadata_lanza_not_found():
    c = AbadSyntheticClient()
    with pytest.raises(ClientNotFoundError):
        c.fetch_expected("JUEZ-E2E-AAA-01", request_metadata=None)


def test_abad_synthetic_metadata_sin_expected_snapshot():
    c = AbadSyntheticClient()
    with pytest.raises(ClientNotFoundError):
        c.fetch_expected("JUEZ-E2E-AAA-01", request_metadata={"synthetic": True})


def test_abad_synthetic_snapshot_invalido():
    c = AbadSyntheticClient()
    with pytest.raises(ClientError):
        c.fetch_expected(
            "JUEZ-E2E-AAA-01",
            request_metadata={"expected_snapshot": {"counts": "no-es-dict"}},
        )


def test_abad_synthetic_artifact_id_mismatch_usa_el_del_request():
    c = AbadSyntheticClient()
    snap_with_diff_id = {**_SAMPLE_SNAPSHOT, "artifact_id": "OTRO-ID"}
    out = c.fetch_expected(
        "JUEZ-E2E-BBB-02",
        request_metadata={"expected_snapshot": snap_with_diff_id},
    )
    # El verifier confía en el artifact_id del request, no del snapshot
    assert out.artifact_id == "JUEZ-E2E-BBB-02"


def test_abad_synthetic_registrado_en_registry():
    from verificador.clientes import get_client
    c = get_client("abad_synthetic")
    assert c.name == "abad_synthetic"


# ─────────────────────────────────────────────────────────────────────────────
# Compat: cliente Abad real acepta request_metadata (lo ignora)
# ─────────────────────────────────────────────────────────────────────────────

def test_abad_real_ignora_request_metadata():
    """El cliente Abad real acepta request_metadata por compat de firma pero
    no lo usa (lee de BD). Lo probamos verificando que el método existe con
    esa firma — no podemos llamarlo sin BD, así que solo introspectamos."""
    import inspect
    from verificador.clientes.abad import AbadClient
    sig = inspect.signature(AbadClient.fetch_expected)
    assert "request_metadata" in sig.parameters
