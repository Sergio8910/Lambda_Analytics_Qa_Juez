"""Monitoreo programado del Juez.

Evalúa periódicamente un conjunto de agentes/flujos (por id) y guarda los
reportes. Pensado para correr como tarea programada (cron / Task Scheduler /
systemd timer / Celery beat) o en modo loop propio.

Por defecto el modo es LIVIANO (estático, sin disparar nada): análisis del
flujo + objetivos + QA de PDF sintético. El modo 'full' (que dispara
conversaciones reales) es opt-in por target.
"""
from .monitor import (
    MonitorTarget,
    evaluate_target,
    load_config,
    run_monitoring_pass,
)

__all__ = [
    "MonitorTarget",
    "evaluate_target",
    "load_config",
    "run_monitoring_pass",
]
