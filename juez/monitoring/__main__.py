"""CLI del monitoreo programado.

Una pasada (para cron / Task Scheduler):
    python -m juez.monitoring --config monitoring.json

Modo loop propio (si no usas un scheduler externo):
    python -m juez.monitoring --config monitoring.json --loop

Variables de entorno: N8N_BASE_URL, N8N_API_KEY (para flujos), OPENAI_API_KEY
(para el diagnóstico/LLM-judge, opcional).
"""
from __future__ import annotations

import argparse
import sys
import time

from dotenv import load_dotenv

from .monitor import load_config, run_monitoring_pass


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="python -m juez.monitoring")
    p.add_argument("--config", required=True, help="Ruta al JSON de configuración del monitoreo")
    p.add_argument("--loop", action="store_true", help="Corre en bucle usando interval_seconds del config")
    p.add_argument("--once", action="store_true", help="Fuerza una sola pasada (default si no hay --loop)")
    args = p.parse_args()

    cfg = load_config(args.config)

    def _una_pasada() -> None:
        res = run_monitoring_pass(cfg)
        print(res["resumen_txt"])
        print(f"\nResumen guardado en: {res['resumen_path']}")

    if args.loop:
        print(f"Monitoreo en loop cada {cfg.interval_seconds}s. Ctrl+C para parar.")
        try:
            while True:
                _una_pasada()
                time.sleep(cfg.interval_seconds)
        except KeyboardInterrupt:
            print("\nMonitoreo detenido.")
            sys.exit(0)
    else:
        _una_pasada()


if __name__ == "__main__":
    main()
