"""E2E del router /verify con cliente y source mockeados.

Verifica el flujo completo:
  POST /verify -> 202 + verification_id
  background thread: cliente.fetch_expected + source.fetch + inspector.inspect + storage
  GET /verify/{id} -> 200 con resultado completo

Sin red ni BD del cliente reales. SQLite local descartable.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from unittest.mock import patch

import pytest


@pytest.fixture
def db_temporal(monkeypatch):
    """Apunta storage a un SQLite temporal y lo inicializa para el test.

    Usa tempfile.mkdtemp en lugar de tmp_path para evitar conflictos
    con plugins de pytest (deepeval, xdist, rerunfailures).
    """
    tmpdir = tempfile.mkdtemp(prefix="verif_test_")
    db_file = os.path.join(tmpdir, "verif_test.db")
    monkeypatch.setattr("verificador.settings.settings.DATABASE_URL", f"sqlite:///{db_file}")
    # storage.py resuelve la URL al importar; forzamos reload para que tome la nueva
    import importlib
    from verificador import storage
    importlib.reload(storage)
    storage.init_db()
    try:
        yield storage
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setattr("verificador.settings.settings.VERIFICADOR_API_KEY", "test-key")
    return "test-key"


@pytest.fixture
def client(db_temporal, api_key):
    """TestClient sobre la app standalone."""
    from fastapi.testclient import TestClient
    # Importar después de monkeypatch para que el router lea el storage recargado
    import importlib
    from verificador import router, app as app_module
    importlib.reload(router)
    importlib.reload(app_module)
    return TestClient(app_module.app)


def _build_expected(pdf_blob_count: int = 9, ambientes=("Cocina", "Sala", "Baño"),
                    fotos_por_amb=None, required_strings=("CONTRATO-OK",)):
    from verificador.schemas import ExpectedSnapshot
    return ExpectedSnapshot(
        artifact_id="INV-1",
        counts={"fotos": pdf_blob_count, "ambientes": len(ambientes)},
        structure={
            "ambientes": list(ambientes),
            **({"fotos_por_ambiente": fotos_por_amb} if fotos_por_amb else {}),
        },
        required_strings=list(required_strings),
    )


def test_health_no_requiere_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["component"] == "verificador"


def test_verify_sin_api_key_devuelve_401(client):
    resp = client.post("/verificador/verify", json={
        "cliente": "abad",
        "artifact_type": "pdf",
        "artifact_id": "INV-1",
        "source": {"type": "drive", "file_id": "x"},
    })
    assert resp.status_code == 401


def test_verify_con_api_key_invalida_devuelve_401(client):
    resp = client.post(
        "/verificador/verify",
        headers={"X-Verifier-Key": "wrong"},
        json={
            "cliente": "abad",
            "artifact_type": "pdf",
            "artifact_id": "INV-1",
            "source": {"type": "drive", "file_id": "x"},
        },
    )
    assert resp.status_code == 401


def test_verify_flujo_completo_ok(client, api_key, pdf_ok):
    """E2E: POST /verify → background corre → GET /verify/{id} retorna OK."""
    expected = _build_expected(pdf_blob_count=9, required_strings=("CONTRATO-OK",))

    # Mock cliente y source para no tocar nada real
    with patch("verificador.clientes.abad.AbadClient.fetch_expected", return_value=expected), \
         patch("verificador.sources.drive.DriveSource.fetch", return_value=pdf_ok):

        resp = client.post(
            "/verificador/verify",
            headers={"X-Verifier-Key": api_key},
            json={
                "cliente": "abad",
                "artifact_type": "pdf",
                "artifact_id": "INV-1",
                "source": {"type": "drive", "file_id": "1aBc"},
                "metadata": {"contrato_id": "CONTRATO-OK"},
            },
        )
        assert resp.status_code == 202, resp.json()
        body = resp.json()
        assert body["status"] == "queued"
        vid = body["verification_id"]
        assert vid.startswith("verif_")

        # Esperar a que el thread termine
        for _ in range(50):
            res = client.get(f"/verificador/verify/{vid}", headers={"X-Verifier-Key": api_key})
            if res.json()["status"] == "completed":
                break
            time.sleep(0.1)

        assert res.status_code == 200
        rb = res.json()
        assert rb["status"] == "completed", rb
        assert rb["verdict"] == "OK", rb
        assert rb["score"] is not None and rb["score"] >= 0.95
        assert any(c["name"] == "integridad" for c in rb["checks"])
        assert any(c["name"] == "conteo_fotos_total" for c in rb["checks"])


def test_verify_idempotencia(client, api_key, pdf_ok):
    expected = _build_expected(pdf_blob_count=9, required_strings=("CONTRATO-OK",))
    with patch("verificador.clientes.abad.AbadClient.fetch_expected", return_value=expected), \
         patch("verificador.sources.drive.DriveSource.fetch", return_value=pdf_ok):
        payload = {
            "cliente": "abad", "artifact_type": "pdf", "artifact_id": "INV-IDEMP",
            "source": {"type": "drive", "file_id": "x"},
        }
        r1 = client.post("/verificador/verify", headers={"X-Verifier-Key": api_key}, json=payload)
        r2 = client.post("/verificador/verify", headers={"X-Verifier-Key": api_key}, json=payload)
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["verification_id"] == r2.json()["verification_id"]


def test_verify_cliente_inexistente_marca_unverifiable(client, api_key, pdf_ok):
    """Si el cliente no está registrado, la verificación queda UNVERIFIABLE."""
    resp = client.post(
        "/verificador/verify",
        headers={"X-Verifier-Key": api_key},
        json={
            "cliente": "cliente_inexistente",
            "artifact_type": "pdf",
            "artifact_id": "INV-X",
            "source": {"type": "drive", "file_id": "x"},
        },
    )
    assert resp.status_code == 202
    vid = resp.json()["verification_id"]
    for _ in range(30):
        r = client.get(f"/verificador/verify/{vid}", headers={"X-Verifier-Key": api_key})
        if r.json()["status"] == "completed":
            break
        time.sleep(0.1)
    assert r.json()["verdict"] == "UNVERIFIABLE"


def test_get_verification_inexistente_404(client, api_key):
    resp = client.get("/verificador/verify/verif_nope", headers={"X-Verifier-Key": api_key})
    assert resp.status_code == 404
