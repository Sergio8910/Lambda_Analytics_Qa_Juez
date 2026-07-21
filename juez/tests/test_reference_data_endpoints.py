"""Smoke tests de los endpoints /api/v1/reference-data/* (ingest, get, list).

Antes de esto, el ingest de datos de referencia parseaba y descartaba (sin
persistencia ni id) -- estos endpoints son nuevos.
"""
from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    import juez.api.reference_store as ref_store_mod

    with tempfile.TemporaryDirectory() as tmp:
        # Store aislado por test -- no contamina outputs/reference_data/ real
        # ni comparte estado entre tests via el singleton global.
        fresh_store = ref_store_mod.ReferenceDataStore(persist_dir=Path(tmp))
        monkeypatch.setattr(ref_store_mod, "_store", fresh_store)
        monkeypatch.setattr(ref_store_mod, "get_reference_store", lambda: fresh_store)
        # router_v1 importa get_reference_store directamente -- parchear tambien ahi.
        import juez.api.router_v1 as router_mod
        monkeypatch.setattr(router_mod, "get_reference_store", lambda: fresh_store)

        from juez.api.main import app
        yield TestClient(app)


def test_ingest_csv_devuelve_id(client: TestClient) -> None:
    contenido = b"nombre,pedido\nJuan Perez,REF-123\n"
    resp = client.post(
        "/api/v1/reference-data/ingest",
        files={"file": ("clientes.csv", io.BytesIO(contenido), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "id" in data and data["id"]
    assert data["resumen"]["n_records"] == 1


def test_ingest_y_luego_get_por_id(client: TestClient) -> None:
    contenido = b"nombre,pedido\nJuan Perez,REF-123\n"
    resp = client.post(
        "/api/v1/reference-data/ingest",
        files={"file": ("clientes.csv", io.BytesIO(contenido), "text/csv")},
    )
    dataset_id = resp.json()["id"]

    resp2 = client.get(f"/api/v1/reference-data/{dataset_id}")
    assert resp2.status_code == 200
    assert resp2.json()["dataset"]["source_name"] == "clientes.csv"


def test_ingest_y_delete(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/reference-data/ingest",
        files={"file": ("clientes.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    dataset_id = resp.json()["id"]
    assert client.delete(f"/api/v1/reference-data/{dataset_id}").status_code == 204
    assert client.get(f"/api/v1/reference-data/{dataset_id}").status_code == 404


def test_delete_inexistente_devuelve_404(client: TestClient) -> None:
    assert client.delete("/api/v1/reference-data/no-existe").status_code == 404


def test_get_id_inexistente_devuelve_404(client: TestClient) -> None:
    resp = client.get("/api/v1/reference-data/no-existe-este-id")
    assert resp.status_code == 404


def test_ingest_payload_template_whatsapp(client: TestClient) -> None:
    payload_whatsapp = {
        "entry": [{"changes": [{"value": {"messages": [{"text": {"body": "{{JUEZ_MENSAJE}}"}}]}}]}],
    }
    resp = client.post(
        "/api/v1/reference-data/ingest",
        files={"file": ("whatsapp.json", io.BytesIO(json.dumps(payload_whatsapp).encode()), "application/json")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["resumen"]["tiene_payload_template"] is True


def test_list_incluye_los_ingeridos(client: TestClient) -> None:
    client.post(
        "/api/v1/reference-data/ingest",
        files={"file": ("a.csv", io.BytesIO(b"x,y\n1,2\n"), "text/csv")},
    )
    resp = client.get("/api/v1/reference-data")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1


def test_ingest_archivo_vacio_devuelve_400(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/reference-data/ingest",
        files={"file": ("vacio.csv", io.BytesIO(b""), "text/csv")},
    )
    assert resp.status_code == 400


def test_ingest_extension_no_soportada_devuelve_400(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/reference-data/ingest",
        files={"file": ("archivo.exe", io.BytesIO(b"contenido"), "application/octet-stream")},
    )
    assert resp.status_code == 400
