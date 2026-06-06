"""Auditar el PDF YA generado de un inventario real (sin disparar el flow).

Camino: lee abad_faciolince (read-only) para obtener:
  - El expected_snapshot del inventario
  - El pdf_drive_file_id del último pdf_aprobacion completado
Llama al Verificador con cliente=abad_synthetic + source=drive + metadata.expected_snapshot.

Cero writes, cero webhook, solo SELECT + 1 GET a Drive + tokens del Verificador.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

from juez.evaluation.contra_agente.synthetic.real_db_source import (
    RealDbError,
    make_real_db_data,
)
from juez.evaluation.contra_agente.verificador_client import (
    VerificadorUnavailable,
    verify_drive_pdf,
)

log = logging.getLogger("juez.audit_real_pdf")


# ── Regex para extraer file_id de una URL de Drive ─────────────────────────
# Formatos típicos:
#   https://drive.google.com/file/d/{FILE_ID}/view
#   https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing
#   https://drive.google.com/open?id={FILE_ID}
#   https://drive.google.com/uc?id={FILE_ID}&export=download
_DRIVE_FILE_ID_PATTERNS = (
    re.compile(r"/file/d/([A-Za-z0-9_-]{8,})"),
    re.compile(r"[?&]id=([A-Za-z0-9_-]{8,})"),
)


def _extract_drive_file_id(value: Optional[str]) -> Optional[str]:
    """Extrae el file_id de un valor que puede ser URL completa o ya un ID.

    Si el valor parece un file_id pelado (solo chars válidos, sin slash ni
    query) y tiene longitud razonable, se devuelve tal cual.
    """
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None

    for pat in _DRIVE_FILE_ID_PATTERNS:
        m = pat.search(v)
        if m:
            return m.group(1)

    # ¿Es un file_id pelado? (sin esquema HTTP, sin /, sin ?, sólo chars válidos)
    if "/" not in v and "?" not in v and v.lower() not in ("none", "null", ""):
        if re.fullmatch(r"[A-Za-z0-9_-]{8,}", v):
            return v

    return None


def _connect_readonly():
    """Abre conexión read-only a la BD de Abad — mismo perfil que real_db_source."""
    import psycopg2  # lazy

    abat_url = os.getenv("ABAT_DB_URL")
    if not abat_url:
        raise RealDbError("ABAT_DB_URL no configurado")
    try:
        conn = psycopg2.connect(
            abat_url,
            connect_timeout=10,
            options="-c statement_timeout=5000",
        )
        conn.set_session(readonly=True, autocommit=False)
        return conn
    except Exception as exc:
        raise RealDbError(f"BD Abad: {type(exc).__name__}: {exc}") from exc


def resolver_ultimo_inventario_con_pdf() -> Optional[int]:
    """Devuelve el `inventario_id` del PDF más reciente generado en producción.

    Estrategia: ordenar `abad_faciolince.pdf_aprobacion` por `fecha_creacion DESC`
    y devolver el primer registro que tenga URL válida en al menos uno de los
    dos campos de Drive (arrendador o propietario).

    Devuelve None si:
      - No hay credencial de BD (`ABAT_DB_URL` ausente).
      - La BD no responde.
      - No hay PDFs en la tabla.
      - Ningún PDF tiene URL parseable.

    El caller debe interpretar None como "no se pudo resolver, sugerir modo manual".
    """
    sql = """
        SELECT inventario_id, pdf_arrendador_drive_url, pdf_propietario_drive_url
        FROM abad_faciolince.pdf_aprobacion
        ORDER BY fecha_creacion DESC
        LIMIT 50
    """
    try:
        conn = _connect_readonly()
    except RealDbError as exc:
        log.warning("resolver_ultimo_inventario_con_pdf: BD no accesible: %s", exc)
        return None

    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    for inv_id, arr_url, prop_url in rows:
        for candidate in (arr_url, prop_url):
            if _extract_drive_file_id(candidate):
                return int(inv_id)
    return None


def _get_pdf_drive_file_id(inventario_id: int) -> Optional[str]:
    """Lee `abad_faciolince.pdf_aprobacion` ordenando por `fecha_creacion DESC LIMIT 1`
    y devuelve el `file_id` extraído de `pdf_arrendador_drive_url` (o
    `pdf_propietario_drive_url` como fallback).

    Retorna None si no hay registros o si ninguno tiene URL válida — la
    función llamadora decide cómo reportar.
    """
    sql = """
        SELECT
          pdf_arrendador_drive_url,
          pdf_propietario_drive_url
        FROM abad_faciolince.pdf_aprobacion
        WHERE inventario_id = %s
        ORDER BY fecha_creacion DESC
        LIMIT 1
    """
    try:
        conn = _connect_readonly()
    except RealDbError as exc:
        log.warning("audit_real_pdf: no se pudo abrir BD: %s", exc)
        return None

    try:
        cur = conn.cursor()
        cur.execute(sql, (inventario_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if row is None:
        return None

    arrendador_url, propietario_url = row
    # Preferimos el del arrendador (es el firmado por el inquilino); si no
    # existe, usamos el del propietario como fallback.
    for candidate in (arrendador_url, propietario_url):
        file_id = _extract_drive_file_id(candidate)
        if file_id:
            return file_id
    return None


def audit_real_inventario(
    inventario_id: int,
    *,
    cliente: str = "abad_synthetic",
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Audita el PDF YA generado para un inventario real.

    Orquesta:
      1. Lee el snapshot esperado desde la BD productiva (read-only).
      2. Resuelve el `drive_file_id` del último pdf_aprobacion del inventario.
      3. Despacha al Verificador con `source=drive` y el snapshot como metadata.

    Args:
        inventario_id: ID del inventario en `abad_faciolince.inventario`.
        cliente: Cliente registrado en el Verificador. Default `abad_synthetic`
            para que el Verificador use el snapshot que le inyectamos vía
            metadata (no toque la BD productiva por su cuenta).
        base_url: Override de la URL del Verificador.

    Returns:
        Dict con `verdict, score, checks, issues, artifact_id, inventario_id,
        pdf_drive_file_id`. Si algo falla antes del despacho, devuelve un dict
        con `error` y `verdict="UNVERIFIABLE"`.
    """
    out: Dict[str, Any] = {
        "inventario_id": inventario_id,
        "pdf_drive_file_id": None,
        "artifact_id": str(inventario_id),
        "verdict": "UNVERIFIABLE",
        "score": None,
        "checks": [],
        "issues": [],
    }

    # 1. Snapshot esperado
    try:
        expected_snapshot, _canonical = make_real_db_data(inventario_id)
    except RealDbError as exc:
        out["error"] = f"No se pudo leer snapshot esperado: {exc}"
        return out
    except Exception as exc:  # defensive: BD prod puede sorprender
        out["error"] = f"Snapshot esperado fallo: {type(exc).__name__}: {exc}"
        return out

    # 2. Drive file id del PDF ya generado
    try:
        pdf_file_id = _get_pdf_drive_file_id(inventario_id)
    except Exception as exc:
        out["error"] = (
            f"No se pudo consultar pdf_aprobacion: {type(exc).__name__}: {exc}"
        )
        return out

    if not pdf_file_id:
        out["error"] = (
            f"Inventario {inventario_id} no tiene un PDF aprobado con drive_url valido "
            f"en abad_faciolince.pdf_aprobacion (ningun registro o URL ilegible)."
        )
        return out

    out["pdf_drive_file_id"] = pdf_file_id

    # 3. Verificador via drive source
    try:
        verdict = verify_drive_pdf(
            cliente=cliente,
            artifact_id=str(inventario_id),
            drive_file_id=pdf_file_id,
            expected_snapshot=expected_snapshot,
            extra_metadata={
                "audit_mode": "real_pdf_drive",
                "inventario_id": inventario_id,
            },
            base_url=base_url,
        )
    except VerificadorUnavailable as exc:
        out["error"] = f"Verificador no disponible: {exc}"
        return out
    except Exception as exc:
        out["error"] = f"Verificador fallo: {type(exc).__name__}: {exc}"
        return out

    out.update({
        "verdict": verdict.get("verdict") or "UNVERIFIABLE",
        "score": verdict.get("score"),
        "checks": verdict.get("checks") or [],
        "issues": verdict.get("issues") or [],
        "elapsed_ms": verdict.get("elapsed_ms"),
        "expected_snapshot": expected_snapshot,
        "raw_verdict": verdict,
    })
    return out
