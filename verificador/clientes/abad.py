"""Adapter de cliente para Abad Faciolince.

Lee READ-ONLY del schema `abad_faciolince` de la BD Postgres del cliente
para construir el `ExpectedSnapshot` que el verificador audita contra el
PDF generado.

Tablas que se consultan (allowlist explícita):
    - abad_faciolince.inventario       (PK: inventario_id)
    - abad_faciolince.ambiente         (FK: inventario_id, room_name, fotos jsonb)
    - abad_faciolince.item_ambiente    (FK: ambiente_id, fotos jsonb)
    - abad_faciolince.contratos_inmuebles  (vista con propietario/arrendatario/direccion)
    - abad_faciolince.pdf_aprobacion   (estado del PDF si se necesita debug)

Nunca se hace INSERT, UPDATE, DELETE. Nunca se consultan otras tablas. La
conexión abre con `SET TRANSACTION READ ONLY` y `statement_timeout` corto
configurado por settings.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

import psycopg2
import psycopg2.extras

from ..schemas import ExpectedSnapshot
from ..settings import settings
from . import register_client
from .base import ClientDBError, ClientNotFoundError

log = logging.getLogger("verificador.clientes.abad")


@contextmanager
def _abad_cursor() -> Iterator[psycopg2.extensions.cursor]:
    """Abre una conexión read-only a la BD de Abad, devuelve cursor, cierra todo.

    Aplica:
      - SET TRANSACTION READ ONLY (defense in depth, además del usuario RO)
      - statement_timeout (no martillar la BD productiva con queries lentos)
      - autocommit=False (asegurar que SET TRANSACTION tiene efecto)
    """
    if not settings.ABAT_DB_URL:
        raise ClientDBError("ABAT_DB_URL no configurado")

    options = f"-c statement_timeout={settings.CLIENT_DB_TIMEOUT_MS}"
    conn = None
    try:
        conn = psycopg2.connect(
            settings.ABAT_DB_URL,
            connect_timeout=10,
            options=options,
        )
        conn.set_session(readonly=True, autocommit=False)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield cur
        finally:
            cur.close()
    except psycopg2.Error as exc:
        raise ClientDBError(f"BD de Abad: {type(exc).__name__}: {exc}") from exc
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _to_int(s: str) -> Optional[int]:
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


class AbadClient:
    """Implementa `BaseClientAdapter` para Abad."""

    name = "abad"

    def fetch_expected(
        self,
        artifact_id: str,
        request_metadata=None,
    ) -> ExpectedSnapshot:
        """Dado un `inventario_id`, construye el snapshot esperado del PDF.

        `request_metadata` se ignora — este cliente lee desde la BD productiva
        de Abad (parámetro presente solo para compatibilidad con el protocolo).

        Si el inventario no existe → `ClientNotFoundError`.
        Si la BD falla → `ClientDBError` (sin reintentos agresivos).
        """
        inventario_id = _to_int(artifact_id)
        if inventario_id is None:
            raise ClientNotFoundError(
                f"artifact_id '{artifact_id}' no es un inventario_id numérico válido para Abad"
            )

        with _abad_cursor() as cur:
            # 1. Inventario + datos del contrato (propietario, arrendatario, dirección)
            cur.execute("""
                SELECT
                  i.inventario_id,
                  i.contrato_id,
                  i.tipo_inventario,
                  i.fecha_inventario,
                  ci.propietario,
                  ci.arrendatario,
                  ci.direccion,
                  ci.doc_propietario,
                  ci.doc_arrendatario
                FROM abad_faciolince.inventario i
                LEFT JOIN abad_faciolince.contratos_inmuebles ci
                    ON ci.contrato_id = i.contrato_id
                WHERE i.inventario_id = %s
            """, (inventario_id,))
            row = cur.fetchone()
            if row is None:
                raise ClientNotFoundError(
                    f"Inventario {inventario_id} no existe en abad_faciolince.inventario"
                )
            info = dict(row)

            # 2. Ambientes + total de fotos por ambiente (suma jsonb_array_length
            #    sobre item_ambiente.fotos, que es donde realmente viven las fotos
            #    según lo confirmado en exploración del schema).
            cur.execute("""
                SELECT
                  a.ambiente_id,
                  a.room_name,
                  a.tipo_ambiente,
                  a.orden,
                  a.total_items,
                  COALESCE(SUM(jsonb_array_length(COALESCE(it.fotos, '[]'::jsonb))), 0) AS n_fotos
                FROM abad_faciolince.ambiente a
                LEFT JOIN abad_faciolince.item_ambiente it
                    ON it.ambiente_id = a.ambiente_id
                WHERE a.inventario_id = %s
                GROUP BY a.ambiente_id, a.room_name, a.tipo_ambiente, a.orden, a.total_items
                ORDER BY a.orden NULLS LAST, a.ambiente_id
            """, (inventario_id,))
            ambientes_rows: List[Dict[str, Any]] = [dict(r) for r in cur.fetchall()]

        # Mapeo a ExpectedSnapshot
        room_names: List[str] = []
        fotos_por_ambiente: Dict[str, int] = {}
        total_fotos = 0
        for r in ambientes_rows:
            room = (r.get("room_name") or "").strip()
            n = int(r.get("n_fotos") or 0)
            if not room:
                continue
            room_names.append(room)
            # Si hay ambientes con el mismo room_name, sumamos (no debería ser normal)
            fotos_por_ambiente[room] = fotos_por_ambiente.get(room, 0) + n
            total_fotos += n

        # Required strings: cosas que el PDF DEBE mencionar para considerarse válido
        required: List[str] = []
        if info.get("contrato_id") is not None:
            required.append(str(info["contrato_id"]))
        if info.get("propietario"):
            required.append(str(info["propietario"]))
        if info.get("arrendatario"):
            required.append(str(info["arrendatario"]))

        snapshot = ExpectedSnapshot(
            artifact_id=str(inventario_id),
            counts={
                "fotos": total_fotos,
                "ambientes": len(room_names),
            },
            structure={
                "ambientes": room_names,
                "fotos_por_ambiente": fotos_por_ambiente,
                "tipo_inventario": info.get("tipo_inventario"),
            },
            required_strings=required,
        )

        log.info(
            "abad.fetch_expected inventario_id=%s ambientes=%d fotos_total=%d",
            inventario_id, len(room_names), total_fotos,
        )
        return snapshot


# Auto-registro
register_client("abad", AbadClient)
