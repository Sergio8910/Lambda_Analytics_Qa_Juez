"""Ejecución asíncrona de verificaciones.

Mucho más simple que `api/jobs.py` del Juez: el estado de cada verificación
ya se persiste en la tabla `verifications` vía `storage.py`. No necesitamos
otro JobStore — solo lanzar la función en un thread daemon.

Si en el futuro se requiere reintento automático, observabilidad o batching,
acá es donde se agrega.
"""
from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Callable

log = logging.getLogger("verificador.jobs")


def run_in_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> threading.Thread:
    """Ejecuta `fn(*args, **kwargs)` en un thread daemon. Retorna el thread.

    Convención: `fn` es responsable de actualizar el estado de la
    verificación en la BD (mark_running → mark_completed/mark_failed). Si
    `fn` levanta una excepción no capturada, este wrapper la loggea pero
    NO toca el estado en BD — la responsabilidad sigue siendo del callee.

    Por qué daemon: si el proceso recibe SIGTERM, los threads daemon mueren
    con él. Eso evita que verificaciones colgadas mantengan el proceso vivo
    indefinidamente. Las verificaciones que mueran a mitad quedarán con
    `status='running'` en BD — un cron de cleanup puede marcarlas `failed`
    (no implementado en MVP, ver hardening).
    """

    def _wrapped() -> None:
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001  — log y seguir, callee maneja BD
            log.exception("Job %s falló sin capturar la excepción: %s",
                          getattr(fn, "__name__", "<anon>"), traceback.format_exc())

    t = threading.Thread(target=_wrapped, daemon=True)
    t.start()
    return t
