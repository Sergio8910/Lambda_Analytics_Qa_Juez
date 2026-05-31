"""Tests para `verificador.retention.purge_old_verifications`.

Setup: SQLite local en directorio temporal. NO usa tmp_path (deshabilitado
en pytest.ini); usamos tempfile.mkdtemp como el resto de los tests.

Cobertura:
  - dry_run cuenta sin borrar.
  - purge real borra solo filas > N días.
  - Idempotente: correr 2 veces no rompe ni borra extra.
  - days=0 borra todo (lo creado hace > 0 segundos).
"""
from __future__ import annotations

import importlib
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

# Estado de módulo: se completa en setup_module / teardown_module.
_TMPDIR: str | None = None
_STORAGE = None
_RETENTION = None


def setup_module(module):
    """Crea un SQLite temporal y recarga storage para apuntar ahí."""
    global _TMPDIR, _STORAGE, _RETENTION

    _TMPDIR = tempfile.mkdtemp(prefix="verif_retention_test_")
    db_file = os.path.join(_TMPDIR, "verif_retention.db")

    # Inyectar URL antes de importar/recargar storage.
    from verificador import settings as settings_module
    settings_module.settings.DATABASE_URL = f"sqlite:///{db_file}"

    from verificador import storage as storage_module
    importlib.reload(storage_module)
    storage_module.init_db()
    _STORAGE = storage_module

    # retention.py importa SessionLocal/Verification de storage; recargarlo
    # después de storage garantiza que vea el engine nuevo.
    from verificador import retention as retention_module
    importlib.reload(retention_module)
    _RETENTION = retention_module


def teardown_module(module):
    global _TMPDIR
    if _TMPDIR and os.path.isdir(_TMPDIR):
        shutil.rmtree(_TMPDIR, ignore_errors=True)
    _TMPDIR = None


def _clear_table():
    """Borra todas las filas para empezar cada test con tabla limpia."""
    from sqlalchemy import delete
    with _STORAGE.SessionLocal() as session:
        session.execute(delete(_STORAGE.Verification))
        session.commit()


def _count_rows() -> int:
    from sqlalchemy import func, select
    with _STORAGE.SessionLocal() as session:
        return int(
            session.execute(
                select(func.count()).select_from(_STORAGE.Verification)
            ).scalar_one()
        )


def _insert_verification(vid: str, created_at: datetime) -> None:
    """Inserta una verification con created_at backdated.

    Usa SessionLocal directo (no `create_pending`) porque queremos controlar
    el timestamp, no usar utcnow().
    """
    with _STORAGE.SessionLocal() as session:
        row = _STORAGE.Verification(
            verification_id=vid,
            cliente="test-cli",
            artifact_type="pdf",
            artifact_id=f"artifact-{vid}",
            source_type="inline",
            source_ref={},
            status="completed",
            verdict=None,
            score=None,
            checks=[],
            issues=[],
            expected_snapshot={},
            extra_metadata={},
            created_at=created_at,
        )
        session.add(row)
        session.commit()


def _seed_three_rows():
    """3 verifications: hoy, hace 50 días, hace 100 días."""
    now = datetime.now(timezone.utc)
    _insert_verification("verif_today", now)
    _insert_verification("verif_50d", now - timedelta(days=50))
    _insert_verification("verif_100d", now - timedelta(days=100))


# ── Tests ────────────────────────────────────────────────────────────────────

def test_dry_run_cuenta_sin_borrar():
    _clear_table()
    _seed_three_rows()

    before = _count_rows()
    assert before == 3

    result = _RETENTION.purge_old_verifications(days=90, dry_run=True)

    after = _count_rows()
    assert after == 3, "dry_run no debe borrar nada"
    assert result["dry_run"] is True
    assert result["deleted_count"] == 1, "solo verif_100d > 90 días"
    assert result["oldest_kept_at"] is not None


def test_purge_real_borra_solo_mas_de_90_dias():
    _clear_table()
    _seed_three_rows()
    assert _count_rows() == 3

    result = _RETENTION.purge_old_verifications(days=90, dry_run=False)

    assert result["dry_run"] is False
    assert result["deleted_count"] == 1
    assert _count_rows() == 2

    # Verificar que las que quedan son las correctas (hoy y 50d).
    from sqlalchemy import select
    with _STORAGE.SessionLocal() as session:
        ids = {
            row.verification_id
            for row in session.execute(select(_STORAGE.Verification)).scalars()
        }
    assert ids == {"verif_today", "verif_50d"}
    assert result["oldest_kept_at"] is not None


def test_idempotente_correr_dos_veces():
    _clear_table()
    _seed_three_rows()

    first = _RETENTION.purge_old_verifications(days=90, dry_run=False)
    assert first["deleted_count"] == 1
    after_first = _count_rows()

    second = _RETENTION.purge_old_verifications(days=90, dry_run=False)
    assert second["deleted_count"] == 0, "segunda corrida no debe borrar nada"
    after_second = _count_rows()

    assert after_first == after_second == 2


def test_days_cero_borra_todo():
    _clear_table()
    _seed_three_rows()
    assert _count_rows() == 3

    result = _RETENTION.purge_old_verifications(days=0, dry_run=False)

    # Las 3 filas tienen created_at hace > 0 segundos (insertadas antes del
    # call). Con days=0 el cutoff es "ahora", así que todas son < cutoff.
    assert result["deleted_count"] == 3
    assert _count_rows() == 0
    # Tabla vacía → oldest_kept_at None.
    assert result["oldest_kept_at"] is None
