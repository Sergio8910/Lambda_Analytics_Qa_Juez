"""Cache filesystem para PDFs sintéticos.

Motivación: regenerar el mismo PDF (mismo `batch_id`, `plan_idx`,
inventario fuente) es determinístico y caro en CPU. Cachear en disco
permite re-correr suites E2E sin pagar el costo.

Diseño:
  - Cache dir configurable via env `JUEZ_PDF_CACHE_DIR`. Default:
    `outputs/synthetic_pdf_cache/` relativo al cwd.
  - Escritura atómica: tempfile + os.replace para no dejar archivos
    parciales si el proceso muere mid-write.
  - `clear_old(days)` borra por mtime — caller decide cada cuánto.

NO se borran archivos automáticamente al `put` (no TTL implícito). El
caller llama `clear_old` cuando le conviene (cron, fin de batch, etc.).
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from typing import Optional

log = logging.getLogger("juez.synthetic.pdf_cache")

_DEFAULT_DIR = os.path.join("outputs", "synthetic_pdf_cache")


def _cache_dir() -> str:
    """Resuelve el cache dir desde env (lazy: lee cada vez).

    Lazy a propósito: permite que tests hagan `monkeypatch.setenv` y la
    próxima llamada respete el valor nuevo. Si fuera global, habría que
    recargar el módulo.
    """
    return os.environ.get("JUEZ_PDF_CACHE_DIR", _DEFAULT_DIR)


def _path_for(key: str) -> str:
    return os.path.join(_cache_dir(), f"{key}.pdf")


def make_cache_key(
    batch_id: str,
    plan_idx: int,
    real_inventario_id: Optional[int] = None,
) -> str:
    """Construye la key canónica del cache.

    Formato: `{batch_id}_{plan_idx}_{real_inv_id|'syn'}`.

    `real_inventario_id=None` significa que el PDF se generó de datos
    100% sintéticos (sin replay de un inventario real). Lo etiquetamos
    como `syn` para distinguirlo de un id real.
    """
    suffix = str(real_inventario_id) if real_inventario_id is not None else "syn"
    return f"{batch_id}_{plan_idx}_{suffix}"


def get(key: str) -> Optional[bytes]:
    """Lee bytes del cache. Retorna None si no existe o no se puede leer."""
    path = _path_for(key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            blob = f.read()
        log.debug("pdf_cache.get hit key=%s bytes=%d", key, len(blob))
        return blob
    except OSError as e:
        log.warning("pdf_cache.get failed key=%s err=%s", key, e)
        return None


def put(key: str, blob: bytes) -> None:
    """Escribe bytes atómicamente.

    Estrategia: NamedTemporaryFile en el mismo dir (para que os.replace
    sea atómico en el mismo filesystem) + replace al destino final.
    """
    cache_dir = _cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    dest = _path_for(key)

    # delete=False: lo cerramos y luego lo movemos a destino. Si el rename
    # falla, lo limpiamos manualmente para no dejar basura.
    tmp = tempfile.NamedTemporaryFile(
        mode="wb", dir=cache_dir, prefix=".tmp_", suffix=".pdf", delete=False
    )
    tmp_path = tmp.name
    try:
        tmp.write(blob)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp_path, dest)
        log.debug("pdf_cache.put ok key=%s bytes=%d path=%s", key, len(blob), dest)
    except Exception:
        # Cleanup best-effort.
        try:
            tmp.close()
        except Exception:
            pass
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def clear_old(days: int = 7) -> int:
    """Borra archivos cuyo mtime sea más viejo que N días.

    Returns:
        Número de archivos borrados.
    """
    cache_dir = _cache_dir()
    if not os.path.isdir(cache_dir):
        return 0

    cutoff = time.time() - (days * 86400)
    deleted = 0
    for name in os.listdir(cache_dir):
        if not name.endswith(".pdf"):
            continue
        path = os.path.join(cache_dir, name)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                os.remove(path)
                deleted += 1
            except OSError as e:
                log.warning("pdf_cache.clear_old failed path=%s err=%s", path, e)

    log.info("pdf_cache.clear_old days=%d deleted=%d dir=%s", days, deleted, cache_dir)
    return deleted
