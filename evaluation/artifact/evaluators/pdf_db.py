"""Evaluador de artefacto PDF + verificacion en DB.

Verifica el PDF producido por un flujo (completitud de fotos, estructura por
ambiente, campos requeridos, integridad) y, si hay DSN de DB configurado,
contrasta contra las filas registradas.

Obtencion del PDF (enchufable, en este orden):
  1. config.pdf_local_path  -> archivo local (pruebas/manual)
  2. trigger_result.response.pdf_base64 / .pdf_url -> inline o descarga
  3. config.pdf_drive_file_id + token Google (GOOGLE_OAUTH_TOKEN) -> Drive
Si no se puede obtener el PDF, NO se penaliza al agente: se reporta como nota de
infraestructura de QA (el webhook de Abat envia el PDF por correo y no lo retorna).
"""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from .. import pdf_checks as pc
from ..protocol import ArtifactContext, ArtifactResult, Issue, mk_issue
from ..registry import evaluator


@evaluator("pdf_db")
class PdfDbEvaluator:
    def __init__(self, **config: Any) -> None:
        self.cfg = config

    # ── Obtencion del PDF ────────────────────────────────────────────────────
    def _obtener_pdf(self, ctx: ArtifactContext) -> Tuple[Optional[bytes], List[str]]:
        notas: List[str] = []
        cfg = self.cfg

        ruta = cfg.get("pdf_local_path")
        if ruta and os.path.exists(ruta):
            with open(ruta, "rb") as f:
                return f.read(), [f"PDF leido de archivo local: {ruta}"]

        resp = (ctx.get("trigger_result") or {}).get("response")
        if isinstance(resp, dict):
            b64 = resp.get("pdf_base64") or resp.get("pdfBase64")
            if b64:
                try:
                    return base64.b64decode(b64), ["PDF decodificado del response (base64)"]
                except Exception as exc:
                    notas.append(f"No se pudo decodificar pdf_base64: {exc}")
            url = resp.get("pdf_url") or resp.get("pdfUrl") or resp.get("url")
            if url:
                try:
                    r = requests.get(url, timeout=60)
                    r.raise_for_status()
                    return r.content, [f"PDF descargado de {url}"]
                except Exception as exc:
                    notas.append(f"No se pudo descargar el PDF de {url}: {exc}")

        file_id = cfg.get("pdf_drive_file_id")
        token = ctx.get("env", {}).get("GOOGLE_OAUTH_TOKEN") or os.getenv("GOOGLE_OAUTH_TOKEN")
        if file_id and token:
            try:
                r = requests.get(
                    f"https://www.googleapis.com/drive/v3/files/{file_id}",
                    params={"alt": "media"},
                    headers={"Authorization": f"Bearer {token}"}, timeout=60,
                )
                r.raise_for_status()
                return r.content, [f"PDF descargado de Drive (file_id={file_id})"]
            except Exception as exc:
                notas.append(f"No se pudo descargar el PDF de Drive: {exc}")
        elif file_id and not token:
            notas.append("Hay pdf_drive_file_id pero falta GOOGLE_OAUTH_TOKEN para Drive")

        notas.append(
            "No se pudo obtener el PDF para inspeccion. El webhook de Abat envia el PDF "
            "por correo y no lo retorna; configura pdf_local_path, pdf_url/pdf_base64 en la "
            "respuesta, o pdf_drive_file_id + GOOGLE_OAUTH_TOKEN para verificar el contenido."
        )
        return None, notas

    # ── Verificacion en DB (opcional, activada por DSN) ──────────────────────
    def _verificar_db(self, ctx: ArtifactContext) -> Tuple[Optional[int], List[Issue], List[str]]:
        cfg = self.cfg
        dsn_env = cfg.get("db_dsn_env", "ABAT_DB_URL")
        dsn = ctx.get("env", {}).get(dsn_env) or os.getenv(dsn_env)
        if not dsn:
            return None, [], [f"Verificacion de DB omitida (sin {dsn_env} configurado)"]
        query_fotos = cfg.get("db_query_fotos")
        if not query_fotos:
            return None, [], ["Verificacion de DB omitida (sin 'db_query_fotos' en la spec)"]
        try:
            import psycopg2
        except ImportError:
            return None, [], ["psycopg2 no instalado — verificacion de DB omitida"]
        params = ctx.get("synthetic_input", {})
        try:
            conn = psycopg2.connect(dsn)
            try:
                with conn.cursor() as cur:
                    cur.execute(query_fotos, params)
                    row = cur.fetchone()
                    n_fotos = int(row[0]) if row and row[0] is not None else 0
            finally:
                conn.close()
            return n_fotos, [], [f"DB: {n_fotos} foto(s) registradas para el inventario"]
        except Exception as exc:
            return None, [], [f"Error consultando la DB: {exc}"]

    # ── Punto de entrada ─────────────────────────────────────────────────────
    def evaluate(self, ctx: ArtifactContext) -> ArtifactResult:
        cfg = self.cfg
        problemas: List[Issue] = []
        notas: List[str] = []
        metricas: Dict[str, Any] = {}
        scores: List[float] = []

        # 1. ¿El disparo fue exitoso?
        tr = ctx.get("trigger_result") or {}
        resp = tr.get("response")
        disparo_ok = bool(tr.get("ok"))
        if isinstance(resp, dict) and "success" in resp:
            disparo_ok = bool(resp.get("success"))
        metricas["disparo_ok"] = disparo_ok
        metricas["http_status"] = tr.get("http_status")
        metricas["latency_ms"] = tr.get("latency_ms")
        if not disparo_ok:
            err = (resp.get("error") if isinstance(resp, dict) else None) or tr.get("error") or "desconocido"
            problemas.append(mk_issue(
                "CRITICO", f"El flujo de generacion del PDF fallo al dispararse: {err}",
                tipo="Artefacto / Disparo"))
            scores.append(0.0)
        else:
            scores.append(1.0)

        # 2. Conteo esperado de fotos: DB (preferido) o spec
        n_db, db_issues, db_notas = self._verificar_db(ctx)
        problemas.extend(db_issues)
        notas.extend(db_notas)
        esperadas = n_db if n_db is not None else int(cfg.get("fotos_esperadas", 0) or 0)
        if n_db is not None:
            metricas["fotos_esperadas_db"] = n_db

        # 3. Inspeccion del PDF (si se puede obtener)
        pdf_bytes, pdf_notas = self._obtener_pdf(ctx)
        notas.extend(pdf_notas)
        if pdf_bytes:
            r_int = pc.verificar_integridad(pdf_bytes)
            scores.append(r_int.score); problemas.extend(r_int.issues); metricas.update(r_int.metricas)

            if r_int.score > 0:
                r_fotos = pc.verificar_conteo_fotos(pdf_bytes, esperadas)
                scores.append(r_fotos.score); problemas.extend(r_fotos.issues); metricas.update(r_fotos.metricas)

                ambientes = cfg.get("ambientes_esperados") or ctx.get("synthetic_input", {}).get("ambientes", [])
                if ambientes:
                    r_amb = pc.verificar_estructura_por_ambiente(pdf_bytes, ambientes)
                    scores.append(r_amb.score); problemas.extend(r_amb.issues); metricas.update(r_amb.metricas)

                campos = list(cfg.get("campos_requeridos", []))
                for k in cfg.get("campos_desde_input", []):
                    v = ctx.get("synthetic_input", {}).get(k)
                    if v:
                        campos.append(str(v))
                if campos:
                    r_campos = pc.verificar_campos_requeridos(pdf_bytes, campos)
                    scores.append(r_campos.score); problemas.extend(r_campos.issues); metricas.update(r_campos.metricas)
        else:
            metricas["pdf_inspeccionado"] = False

        metricas["contenido_verificado"] = bool(pdf_bytes)
        score = round(sum(scores) / len(scores) * 100, 1) if scores else 0.0
        return {
            "nombre": "PDF / DB",
            "score": score,
            "problemas": problemas,
            "reporte": "",   # lo arma report.generar_reporte_artefacto
            "metricas": {**metricas, "notas": notas},
        }
