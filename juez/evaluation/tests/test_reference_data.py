"""Tests de la ingesta de información previa (datos de referencia)."""
from __future__ import annotations

import io
import json

import pytest

from juez.evaluation.reference_data import (
    ParseError,
    lookup_records,
    parse_reference_file,
    verify_value,
)


# --------------------------------------------------------------------------- CSV
def test_parse_csv():
    csv_bytes = b"producto,precio\nleche,4500\narroz,3200\n"
    ds = parse_reference_file("precios.csv", csv_bytes)
    assert ds.format == "csv"
    assert ds.columns == ["producto", "precio"]
    assert ds.n_records == 2
    assert ds.records[0] == {"producto": "leche", "precio": "4500"}


def test_parse_csv_detecta_punto_y_coma():
    ds = parse_reference_file("x.csv", b"a;b;c\n1;2;3\n")
    assert ds.columns == ["a", "b", "c"]
    assert ds.records[0]["b"] == "2"


# --------------------------------------------------------------------------- JSON
def test_parse_json_lista():
    data = json.dumps([{"id": 1, "estado": "abierto"}, {"id": 2, "estado": "cerrado"}]).encode()
    ds = parse_reference_file("tickets.json", data)
    assert ds.format == "json"
    assert ds.n_records == 2
    assert "estado" in ds.columns


def test_parse_json_objeto_con_lista():
    data = json.dumps({"tickets": [{"id": 1}, {"id": 2}]}).encode()
    ds = parse_reference_file("t.json", data)
    assert ds.n_records == 2
    assert any("tickets" in n for n in ds.notas)


def test_parse_json_invalido_lanza():
    with pytest.raises(ParseError):
        parse_reference_file("x.json", b"{no es json")


# ------------------------------------------------------------- payload_template
def test_parse_json_con_marcador_se_detecta_como_payload_template():
    """Un JSON que contiene {{JUEZ_MENSAJE}} en algun valor anidado se guarda
    como payload_template -- ejemplo real de un webhook (ej. WhatsApp
    Business API) en vez de records tabulares."""
    payload_whatsapp = {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{"from": "573001234567", "text": {"body": "{{JUEZ_MENSAJE}}"}}]
                }
            }]
        }],
    }
    ds = parse_reference_file("whatsapp_ejemplo.json", json.dumps(payload_whatsapp).encode())
    assert ds.payload_template is not None
    assert ds.payload_template["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] == "{{JUEZ_MENSAJE}}"
    assert ds.n_records == 0


def test_parse_json_sin_marcador_no_es_payload_template():
    ds = parse_reference_file("normal.json", json.dumps({"cliente": "Juan", "pedido": "REF-123"}).encode())
    assert ds.payload_template is None
    assert ds.n_records == 1


# --------------------------------------------------------------------------- TXT
def test_parse_txt():
    ds = parse_reference_file("notas.txt", "línea uno\nlínea dos".encode("utf-8"))
    assert ds.format == "txt"
    assert "línea uno" in ds.text
    assert ds.n_records == 0


# --------------------------------------------------------------------------- XLSX
def test_parse_xlsx():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["producto", "precio"])
    ws.append(["leche", 4500])
    ws.append(["arroz", 3200])
    buf = io.BytesIO()
    wb.save(buf)
    ds = parse_reference_file("precios.xlsx", buf.getvalue())
    assert ds.format == "xlsx"
    assert ds.columns == ["producto", "precio"]
    assert ds.n_records == 2
    assert ds.records[0]["precio"] == 4500


# --------------------------------------------------------------------------- DOCX
def test_parse_docx_con_tabla():
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_paragraph("Información previa del cliente")
    tabla = doc.add_table(rows=3, cols=2)
    datos = [["producto", "precio"], ["leche", "4500"], ["arroz", "3200"]]
    for r, fila in enumerate(datos):
        for c, val in enumerate(fila):
            tabla.cell(r, c).text = val
    buf = io.BytesIO()
    doc.save(buf)
    ds = parse_reference_file("info.docx", buf.getvalue())
    assert ds.format == "docx"
    assert "Información previa" in ds.text
    assert ds.columns == ["producto", "precio"]
    assert ds.n_records == 2


# --------------------------------------------------------------------------- extensión no soportada
def test_extension_no_soportada():
    with pytest.raises(ParseError):
        parse_reference_file("foto.png", b"\x89PNG")


# --------------------------------------------------------------------------- lookup / verify (Juzgado)
def _dataset_precios():
    return parse_reference_file("p.csv", b"producto,precio,stock\nleche,4500,si\narroz,3200,no\n")


def test_lookup_encuentra():
    ds = _dataset_precios()
    res = lookup_records(ds, {"producto": "leche"})
    assert res.encontrado
    assert res.n_coincidencias == 1
    assert res.coincidencias[0]["precio"] == "4500"


def test_lookup_tolerante_a_mayusculas_y_columna():
    ds = _dataset_precios()
    # columna 'Producto' (mayúscula) y valor 'LECHE' deben matchear igual
    res = lookup_records(ds, {"Producto": "LECHE"})
    assert res.encontrado


def test_verify_value_verdadero_y_falso():
    ds = _dataset_precios()
    # El agente dice que la leche cuesta 4500 -> verídico
    assert verify_value(ds, "precio", "4500", where={"producto": "leche"}) is True
    # El agente dice que la leche cuesta 9999 -> NO verídico
    assert verify_value(ds, "precio", "9999", where={"producto": "leche"}) is False
