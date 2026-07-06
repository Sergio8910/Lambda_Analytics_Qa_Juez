"""CLI de La Colmena:  python -m juez.colmena --config proyecto.json

El JSON: {"project_id": "...", "componentes": [{kind, nombre, workflow_json|workflow_id,
objetivos?, prompt?}, ...]}. Genera reporte TXT en outputs/ y lo imprime.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from .colmena import Componente, render_colmena_report, run_colmena


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="python -m juez.colmena")
    p.add_argument("--config", required=True, help="JSON del proyecto")
    p.add_argument("--incluir-dinamicas", action="store_true", help="(placeholder) obreras dinámicas")
    args = p.parse_args()

    data = json.loads(Path(args.config).read_text(encoding="utf-8"))
    componentes = [Componente(**c) for c in data.get("componentes", [])]
    r = run_colmena(data.get("project_id", "proyecto"), componentes, incluir_dinamicas=args.incluir_dinamicas)

    reporte = render_colmena_report(r)
    print(reporte)
    out = Path("outputs"); out.mkdir(exist_ok=True)
    path = out / f"colmena_{r.project_id}.txt"
    path.write_text(reporte, encoding="utf-8")
    print(f"\nReporte guardado en: {path}")


if __name__ == "__main__":
    main()
