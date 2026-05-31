"""Tests para `pdf_cache`.

NO usa pytest tmp_path (deshabilitado en pytest.ini). Usa tempfile.mkdtemp
+ monkeypatch sobre env `JUEZ_PDF_CACHE_DIR` para apuntar el cache a un
directorio aislado por test.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time

import pytest

from juez.evaluation.contra_agente.synthetic import pdf_cache


@pytest.fixture
def cache_dir(monkeypatch):
    """Directorio aislado para el cache + cleanup al final."""
    tmpdir = tempfile.mkdtemp(prefix="pdf_cache_test_")
    monkeypatch.setenv("JUEZ_PDF_CACHE_DIR", tmpdir)
    try:
        yield tmpdir
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── put + get round-trip ─────────────────────────────────────────────────────

def test_put_get_round_trip(cache_dir):
    blob = b"%PDF-1.4 fake pdf bytes for testing\n%%EOF"
    pdf_cache.put("test_key_1", blob)

    # Archivo debe existir físicamente.
    expected_path = os.path.join(cache_dir, "test_key_1.pdf")
    assert os.path.isfile(expected_path)

    retrieved = pdf_cache.get("test_key_1")
    assert retrieved == blob


def test_put_overwrites_existing(cache_dir):
    pdf_cache.put("same_key", b"first version")
    pdf_cache.put("same_key", b"second version")
    assert pdf_cache.get("same_key") == b"second version"


def test_put_atomic_no_temp_files_dejados(cache_dir):
    """Tras un put exitoso no deben quedar tempfiles."""
    pdf_cache.put("atomic_key", b"some bytes")
    leftovers = [
        f for f in os.listdir(cache_dir)
        if f.startswith(".tmp_")
    ]
    assert leftovers == [], f"tempfiles leftover: {leftovers}"


# ── get de key inexistente ───────────────────────────────────────────────────

def test_get_inexistente_retorna_none(cache_dir):
    assert pdf_cache.get("never_written") is None


def test_get_inexistente_no_crashea_si_dir_no_existe(monkeypatch):
    """Cache dir que no existe: get debe retornar None sin crashear."""
    fake_dir = os.path.join(
        tempfile.gettempdir(), "pdf_cache_does_not_exist_xyzzy_12345"
    )
    # Asegurar que no existe.
    if os.path.exists(fake_dir):
        shutil.rmtree(fake_dir, ignore_errors=True)
    monkeypatch.setenv("JUEZ_PDF_CACHE_DIR", fake_dir)
    assert pdf_cache.get("any_key") is None


# ── clear_old con timestamps ─────────────────────────────────────────────────

def test_clear_old_respeta_timestamps(cache_dir):
    """Solo se borran los archivos cuyo mtime es más viejo que el cutoff."""
    pdf_cache.put("fresh", b"fresh bytes")
    pdf_cache.put("old", b"old bytes")
    pdf_cache.put("ancient", b"ancient bytes")

    now = time.time()
    # Backdate "old" a 3 días atrás (no debe borrarse con days=7).
    os.utime(os.path.join(cache_dir, "old.pdf"), (now - 3 * 86400, now - 3 * 86400))
    # Backdate "ancient" a 10 días atrás (debe borrarse con days=7).
    os.utime(
        os.path.join(cache_dir, "ancient.pdf"),
        (now - 10 * 86400, now - 10 * 86400),
    )

    deleted = pdf_cache.clear_old(days=7)
    assert deleted == 1

    assert pdf_cache.get("fresh") == b"fresh bytes"
    assert pdf_cache.get("old") == b"old bytes"
    assert pdf_cache.get("ancient") is None


def test_clear_old_dir_inexistente_retorna_cero(monkeypatch):
    fake_dir = os.path.join(
        tempfile.gettempdir(), "pdf_cache_clear_old_no_dir_xyzzy_98765"
    )
    if os.path.exists(fake_dir):
        shutil.rmtree(fake_dir, ignore_errors=True)
    monkeypatch.setenv("JUEZ_PDF_CACHE_DIR", fake_dir)
    assert pdf_cache.clear_old(days=7) == 0


def test_clear_old_ignora_no_pdf(cache_dir):
    """Solo toca archivos *.pdf, no .txt u otros."""
    pdf_cache.put("real_pdf", b"pdf bytes")
    txt_path = os.path.join(cache_dir, "extraño.txt")
    with open(txt_path, "wb") as f:
        f.write(b"texto")
    # Backdate ambos a 30 días atrás.
    old = time.time() - 30 * 86400
    os.utime(os.path.join(cache_dir, "real_pdf.pdf"), (old, old))
    os.utime(txt_path, (old, old))

    deleted = pdf_cache.clear_old(days=7)
    assert deleted == 1  # solo el .pdf
    assert os.path.isfile(txt_path), "archivos no-PDF deben permanecer"


# ── make_cache_key determinístico ────────────────────────────────────────────

def test_make_cache_key_deterministico():
    k1 = pdf_cache.make_cache_key("batch-A", 0, 12345)
    k2 = pdf_cache.make_cache_key("batch-A", 0, 12345)
    assert k1 == k2 == "batch-A_0_12345"


def test_make_cache_key_sin_real_inventario():
    k = pdf_cache.make_cache_key("batch-B", 3)
    assert k == "batch-B_3_syn"


def test_make_cache_key_distintos_argumentos_distintos_keys():
    assert pdf_cache.make_cache_key("X", 0) != pdf_cache.make_cache_key("Y", 0)
    assert pdf_cache.make_cache_key("X", 0) != pdf_cache.make_cache_key("X", 1)
    assert pdf_cache.make_cache_key("X", 0, 1) != pdf_cache.make_cache_key("X", 0, 2)
    assert pdf_cache.make_cache_key("X", 0, None) != pdf_cache.make_cache_key("X", 0, 1)
