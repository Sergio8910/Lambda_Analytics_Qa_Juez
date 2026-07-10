"""Tests del almacen persistente de datasets de referencia (subir una vez,
reusar en muchas corridas/monitores) -- antes de esto, el endpoint parseaba
y descartaba, sin ID ni persistencia."""
from __future__ import annotations

import tempfile
from pathlib import Path

from juez.api.reference_store import ReferenceDataStore
from juez.evaluation.reference_data.models import ReferenceDataset


def _dataset(nombre: str = "clientes.csv") -> ReferenceDataset:
    return ReferenceDataset(
        source_name=nombre, format="csv", columns=["nombre", "pedido"],
        records=[{"nombre": "Juan Perez", "pedido": "REF-123"}],
    )


def test_save_devuelve_id_y_se_puede_recuperar():
    with tempfile.TemporaryDirectory() as tmp:
        store = ReferenceDataStore(persist_dir=Path(tmp))
        entry = store.save(_dataset())
        assert entry["id"]
        recuperado = store.get(entry["id"])
        assert recuperado is not None
        assert recuperado.source_name == "clientes.csv"
        assert recuperado.records[0]["nombre"] == "Juan Perez"


def test_get_id_inexistente_devuelve_none():
    with tempfile.TemporaryDirectory() as tmp:
        store = ReferenceDataStore(persist_dir=Path(tmp))
        assert store.get("no-existe") is None


def test_list_ordena_por_mas_reciente(monkeypatch):
    """Fuerza timestamps distintos y crecientes -- dos save() consecutivos
    pueden caer en el mismo microsegundo real y volver el orden ambiguo."""
    import juez.api.reference_store as ref_store_mod

    tiempos = iter(["2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00"])
    monkeypatch.setattr(ref_store_mod, "_now_iso", lambda: next(tiempos))

    with tempfile.TemporaryDirectory() as tmp:
        store = ReferenceDataStore(persist_dir=Path(tmp))
        e1 = store.save(_dataset("primero.csv"))
        e2 = store.save(_dataset("segundo.csv"))
        items = store.list()
        assert items[0]["id"] == e2["id"]
        assert items[1]["id"] == e1["id"]


def test_persistencia_sobrevive_a_reinicio():
    """Simula un reinicio del servidor: un store nuevo, apuntando al mismo
    directorio, debe recuperar los datasets ya guardados."""
    with tempfile.TemporaryDirectory() as tmp:
        store1 = ReferenceDataStore(persist_dir=Path(tmp))
        entry = store1.save(_dataset())

        store2 = ReferenceDataStore(persist_dir=Path(tmp))
        recuperado = store2.get(entry["id"])
        assert recuperado is not None
        assert recuperado.source_name == "clientes.csv"


def test_payload_template_se_persiste_correctamente():
    ds = ReferenceDataset(
        source_name="whatsapp.json", format="json",
        payload_template={"entry": [{"changes": [{"value": {"text": "{{JUEZ_MENSAJE}}"}}]}]},
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = ReferenceDataStore(persist_dir=Path(tmp))
        entry = store.save(ds)
        recuperado = store.get(entry["id"])
        assert recuperado.payload_template is not None
        assert recuperado.payload_template["entry"][0]["changes"][0]["value"]["text"] == "{{JUEZ_MENSAJE}}"
