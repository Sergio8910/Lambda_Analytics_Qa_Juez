"""Lee datos REALES de la BD de Abad para alimentar el caso e2e sintético.

Garantías operacionales:
  - Conexión read-only (SET TRANSACTION READ ONLY) + statement_timeout corto.
  - SOLO consultas SELECT contra tablas conocidas del schema abad_faciolince.
  - Cero escrituras, cero modificaciones.
  - El Verificador NO se entera de la BD productiva — el snapshot se le
    inyecta vía metadata del request (sigue usando cliente `abad_synthetic`).

Tablas consultadas (allowlist explícita):
  - abad_faciolince.inventario        (PK inventario_id)
  - abad_faciolince.ambiente          (FK inventario_id; room_name)
  - abad_faciolince.item_ambiente     (FK ambiente_id; fotos jsonb)
  - abad_faciolince.contratos_inmuebles (propietario, arrendatario, direccion)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Tuple

log = logging.getLogger("juez.synthetic.real_db_source")


class RealDbError(Exception):
    """Cualquier problema con la BD de Abad — el caso e2e cae a sintético puro."""


def _connect():
    """Abre una conexión read-only con timeout corto a la BD de Abad."""
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


def listar_inventarios_disponibles() -> List[Dict[str, Any]]:
    """Devuelve inventarios reales con conteo de ambientes/fotos para que el
    usuario elija uno representativo. Solo metadata, no nombres de personas."""
    sql = """
        SELECT
          i.inventario_id,
          i.contrato_id,
          i.tipo_inventario,
          COUNT(DISTINCT a.ambiente_id) AS ambientes,
          COALESCE(SUM(jsonb_array_length(COALESCE(it.fotos, '[]'::jsonb))), 0) AS fotos
        FROM abad_faciolince.inventario i
        LEFT JOIN abad_faciolince.ambiente a ON a.inventario_id = i.inventario_id
        LEFT JOIN abad_faciolince.item_ambiente it ON it.ambiente_id = a.ambiente_id
        GROUP BY i.inventario_id, i.contrato_id, i.tipo_inventario
        ORDER BY i.inventario_id
    """
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cur.close()
        return [
            {"inventario_id": r[0], "contrato_id": r[1], "tipo_inventario": r[2],
             "ambientes": int(r[3] or 0), "fotos": int(r[4] or 0)}
            for r in rows
        ]
    finally:
        conn.close()


def make_real_db_data(inventario_id: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Lee BD real y arma `(expected_snapshot, canonical_data)` para el e2e.

    Misma shape que `snapshot_factory.make_synthetic_data` para que el resto
    del pipeline (generator, MockAgent, pdf_builder, Verificador) no cambie.

    Raises:
        RealDbError: si la conexión falla, el inventario no existe o queda vacío.
    """
    conn = _connect()
    try:
        cur = conn.cursor()

        # 1) Cabecera del inventario + datos del contrato
        cur.execute("""
            SELECT
              i.inventario_id, i.contrato_id, i.tipo_inventario, i.fecha_inventario,
              ci.propietario, ci.arrendatario, ci.direccion,
              ci.doc_propietario, ci.doc_arrendatario
            FROM abad_faciolince.inventario i
            LEFT JOIN abad_faciolince.contratos_inmuebles ci
                ON ci.contrato_id = i.contrato_id
            WHERE i.inventario_id = %s
        """, (inventario_id,))
        row = cur.fetchone()
        if row is None:
            raise RealDbError(f"Inventario {inventario_id} no existe en abad_faciolince.inventario")

        (inv_id, contrato_id, tipo_inv, fecha_inv,
         propietario, arrendatario, direccion,
         doc_propietario, doc_arrendatario) = row

        # 2) Ambientes + fotos por ambiente (vienen de item_ambiente.fotos jsonb)
        cur.execute("""
            SELECT
              a.ambiente_id, a.room_name,
              COALESCE(SUM(jsonb_array_length(COALESCE(it.fotos, '[]'::jsonb))), 0) AS n_fotos
            FROM abad_faciolince.ambiente a
            LEFT JOIN abad_faciolince.item_ambiente it ON it.ambiente_id = a.ambiente_id
            WHERE a.inventario_id = %s
            GROUP BY a.ambiente_id, a.room_name, a.orden
            ORDER BY a.orden NULLS LAST, a.ambiente_id
        """, (inventario_id,))
        ambientes_rows = cur.fetchall()
        cur.close()

        if not ambientes_rows:
            raise RealDbError(
                f"Inventario {inventario_id} no tiene ambientes registrados — caso e2e no útil"
            )

        room_names: List[str] = []
        fotos_por_ambiente: Dict[str, int] = {}
        total_fotos = 0
        for amb_id, room, n in ambientes_rows:
            r = (room or "").strip()
            if not r:
                continue
            n_int = int(n or 0)
            room_names.append(r)
            fotos_por_ambiente[r] = fotos_por_ambiente.get(r, 0) + n_int
            total_fotos += n_int

        # artifact_id = inventario_id real (el cliente Abad real sabe interpretarlo).
        # En modo "real-A" no inventamos JUEZ-E2E-XXXX: usamos el ID que la BD ya tiene.
        artifact_id = str(inventario_id)

        # required_strings: lo que el PDF DEBE mencionar para ser válido
        required: List[str] = [str(contrato_id), str(tipo_inv or "INICIAL")]
        if propietario:
            required.append(str(propietario))
        if arrendatario:
            required.append(str(arrendatario))

        expected_snapshot = {
            "artifact_id": artifact_id,
            "counts": {
                "fotos": total_fotos,
                "ambientes": len(room_names),
            },
            "structure": {
                "ambientes": room_names,
                "fotos_por_ambiente": fotos_por_ambiente,
                "tipo_inventario": str(tipo_inv or "INICIAL"),
            },
            "required_strings": required,
        }

        canonical_data = {
            "source": "real_db",
            "inventario_id": int(inv_id),
            "contrato_id": str(contrato_id),
            "tipo_inventario": str(tipo_inv or "INICIAL"),
            "fecha_inventario": fecha_inv.isoformat() if fecha_inv else None,
            "propietario": str(propietario) if propietario else "Propietario (sin nombre en BD)",
            "arrendatario": str(arrendatario) if arrendatario else "Arrendatario (sin nombre en BD)",
            "direccion": str(direccion) if direccion else "",
            "doc_propietario": str(doc_propietario) if doc_propietario else "",
            "doc_arrendatario": str(doc_arrendatario) if doc_arrendatario else "",
            "ambientes": room_names,
            "fotos_por_ambiente": fotos_por_ambiente,
            "total_fotos": total_fotos,
        }

        log.info(
            "real_db_source.ok inventario_id=%d contrato_id=%s ambientes=%d fotos=%d",
            inv_id, contrato_id, len(room_names), total_fotos,
        )
        return expected_snapshot, canonical_data
    finally:
        conn.close()
