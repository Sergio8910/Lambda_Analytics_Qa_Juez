"""Storage del Verificador con SQLAlchemy 2.x.

Backend:
  - Si `DATABASE_URL` está set y apunta a Postgres, usa esa BD con schema
    `verificador` (aislado de las tablas del Juez).
  - Default: SQLite local en `outputs/verificador.db` — sin dependencias de
    infra para correr tests y desarrollo local.

Tabla principal: `verifications`. Idempotencia por `(cliente, artifact_id)`.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Column, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine,
    select, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# JSONB nativo en Postgres, JSON portable en SQLite
try:
    from sqlalchemy.dialects.postgresql import JSONB as _PGJSONB  # type: ignore
except Exception:
    _PGJSONB = None  # SQLAlchemy estará disponible pero el dialect quizá no
from sqlalchemy import JSON as _JSON
from sqlalchemy.types import TypeDecorator

from .schemas import (
    CheckResult,
    Issue,
    VerificationResult,
    VerificationStatus,
    Verdict,
)
from .settings import settings

log = logging.getLogger("verificador.storage")


class JSONBOrJSON(TypeDecorator):
    """Usa JSONB en Postgres, JSON en otros backends. Transparente al código."""
    impl = _JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql" and _PGJSONB is not None:
            return dialect.type_descriptor(_PGJSONB())
        return dialect.type_descriptor(_JSON())


# ─────────────────────────────────────────────────────────────────────────────
# Engine y sesión
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_database_url() -> str:
    """Resuelve la URL de la BD. Default: SQLite en outputs/verificador.db."""
    if settings.DATABASE_URL:
        return settings.DATABASE_URL
    os.makedirs("outputs", exist_ok=True)
    db_path = os.path.abspath(os.path.join("outputs", "verificador.db"))
    return f"sqlite:///{db_path}"


_DB_URL = _resolve_database_url()
_IS_POSTGRES = _DB_URL.startswith("postgresql")

_engine_kwargs: Dict[str, Any] = {"future": True}
if _DB_URL.startswith("sqlite"):
    # SQLite requiere check_same_thread=False para usarse con JobStore en threads
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(_DB_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


class Base(DeclarativeBase):
    """Declarative base con schema 'verificador' solo si es Postgres."""
    # En Postgres, todas las tablas viven en el schema 'verificador'.
    # En SQLite no hay schemas, así que se omite.
    if _IS_POSTGRES:
        __table_args__ = {"schema": settings.DB_SCHEMA}


# ─────────────────────────────────────────────────────────────────────────────
# Modelo
# ─────────────────────────────────────────────────────────────────────────────

class Verification(Base):
    __tablename__ = "verifications"

    verification_id = Column(String(32), primary_key=True)
    cliente = Column(String(64), nullable=False, index=True)
    artifact_type = Column(String(32), nullable=False, index=True)
    artifact_id = Column(String(128), nullable=False)
    source_type = Column(String(32), nullable=False)
    source_ref = Column(JSONBOrJSON, nullable=False, default=dict)

    status = Column(String(16), nullable=False, index=True)
    verdict = Column(String(16), nullable=True, index=True)
    score = Column(Float, nullable=True)

    checks = Column(JSONBOrJSON, nullable=False, default=list)
    issues = Column(JSONBOrJSON, nullable=False, default=list)
    expected_snapshot = Column(JSONBOrJSON, nullable=False, default=dict)
    extra_metadata = Column(JSONBOrJSON, nullable=False, default=dict)

    artifact_size_bytes = Column(Integer, nullable=True)
    elapsed_ms = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("cliente", "artifact_id", name="uq_cliente_artifact_id"),
        (Base.__table_args__ if _IS_POSTGRES else {}),
    )


def init_db() -> None:
    """Crea la tabla si no existe. Idempotente. En Postgres crea el schema primero."""
    if _IS_POSTGRES:
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS {settings.DB_SCHEMA}'))
    Base.metadata.create_all(bind=engine)
    log.info("storage.init_db ok backend=%s", "postgres" if _IS_POSTGRES else "sqlite")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers ID y mapping
# ─────────────────────────────────────────────────────────────────────────────

def new_verification_id() -> str:
    """`verif_<uuid4_hex_12>` — corto pero único."""
    return f"verif_{uuid.uuid4().hex[:12]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_jsonable(value: Any) -> Any:
    """Pydantic v2 → dict; enums → value; deja primitivos en paz."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value") and hasattr(value, "name"):
        return value.value
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _to_result(row: Verification) -> VerificationResult:
    """Mapea fila DB a VerificationResult Pydantic."""
    return VerificationResult(
        verification_id=row.verification_id,
        cliente=row.cliente,
        artifact_type=row.artifact_type,
        artifact_id=row.artifact_id,
        status=VerificationStatus(row.status),
        verdict=Verdict(row.verdict) if row.verdict else None,
        score=row.score,
        checks=[CheckResult(**c) for c in (row.checks or [])],
        issues=[Issue(**i) for i in (row.issues or [])],
        metadata=row.extra_metadata or {},
        artifact_size_bytes=row.artifact_size_bytes,
        elapsed_ms=row.elapsed_ms,
        error=row.error,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_pending(
    cliente: str,
    artifact_type: str,
    artifact_id: str,
    source_type: str,
    source_ref: Dict[str, Any],
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Crea una verificación en `queued`. Si ya existe `(cliente, artifact_id)`,
    retorna el ID existente (idempotencia)."""
    with SessionLocal() as session:
        # Lookup existente
        existing = session.execute(
            select(Verification).where(
                Verification.cliente == cliente,
                Verification.artifact_id == artifact_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing.verification_id

        vid = new_verification_id()
        row = Verification(
            verification_id=vid,
            cliente=cliente,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            source_type=source_type,
            source_ref=source_ref,
            status=VerificationStatus.QUEUED.value,
            verdict=None,
            score=None,
            checks=[],
            issues=[],
            expected_snapshot={},
            extra_metadata=extra_metadata or {},
            created_at=_utcnow(),
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            # Race: alguien insertó entre nuestro lookup y commit. Devolver el existente.
            session.rollback()
            existing = session.execute(
                select(Verification).where(
                    Verification.cliente == cliente,
                    Verification.artifact_id == artifact_id,
                )
            ).scalar_one()
            return existing.verification_id
        return vid


def mark_running(verification_id: str) -> None:
    with SessionLocal() as session:
        session.execute(
            update(Verification)
            .where(Verification.verification_id == verification_id)
            .values(status=VerificationStatus.RUNNING.value)
        )
        session.commit()


def mark_completed(
    verification_id: str,
    verdict: Verdict,
    score: float,
    checks: List[CheckResult],
    issues: List[Issue],
    expected_snapshot: Dict[str, Any],
    artifact_size_bytes: Optional[int] = None,
    elapsed_ms: Optional[int] = None,
) -> None:
    with SessionLocal() as session:
        session.execute(
            update(Verification)
            .where(Verification.verification_id == verification_id)
            .values(
                status=VerificationStatus.COMPLETED.value,
                verdict=verdict.value,
                score=score,
                checks=[_to_jsonable(c) for c in checks],
                issues=[_to_jsonable(i) for i in issues],
                expected_snapshot=_to_jsonable(expected_snapshot),
                artifact_size_bytes=artifact_size_bytes,
                elapsed_ms=elapsed_ms,
                completed_at=_utcnow(),
            )
        )
        session.commit()


def mark_failed(verification_id: str, error: str, elapsed_ms: Optional[int] = None) -> None:
    with SessionLocal() as session:
        session.execute(
            update(Verification)
            .where(Verification.verification_id == verification_id)
            .values(
                status=VerificationStatus.FAILED.value,
                error=error,
                elapsed_ms=elapsed_ms,
                completed_at=_utcnow(),
            )
        )
        session.commit()


def get(verification_id: str) -> Optional[VerificationResult]:
    with SessionLocal() as session:
        row = session.get(Verification, verification_id)
        return _to_result(row) if row else None


def get_by_artifact(cliente: str, artifact_id: str) -> Optional[VerificationResult]:
    with SessionLocal() as session:
        row = session.execute(
            select(Verification).where(
                Verification.cliente == cliente,
                Verification.artifact_id == artifact_id,
            )
        ).scalar_one_or_none()
        return _to_result(row) if row else None
