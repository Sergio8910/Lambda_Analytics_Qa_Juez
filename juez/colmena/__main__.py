"""CLI de La Colmena.

Uso:
  python -m juez.colmena --project proyecto.json
  python -m juez.colmena --project proyecto.json --auto-fix
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from .auto_fix_agent import render_auto_fix_agent_report
from .colmena import Componente, render_colmena_report
from .project_evaluator import render_project_report, write_project_outputs
from .reina import ReinaColmena


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="python -m juez.colmena")
    p.add_argument("--project", "--config", dest="project", required=True, help="JSON del proyecto o carpeta")
    p.add_argument("--incluir-dinamicas", action="store_true", help="Corre obreras dinamicas")
    p.add_argument("--auto-fix", action="store_true", help="Evalua, aplica fixes, re-evalua e itera")
    p.add_argument("--max-iterations", type=int, default=5, help="Maximo de iteraciones de Auto-Fix")
    p.add_argument("--min-confidence", type=float, default=0.80, help="Confianza minima para aplicar fixes")
    p.add_argument("--no-apply", action="store_true", help="Re-evalua en memoria, sin escribir cambios")
    p.add_argument("--no-git", action="store_true", help="No crear backup branch ni commits automaticos")
    args = p.parse_args()

    project_path = Path(args.project)
    if not project_path.exists():
        raise SystemExit(f"El proyecto no existe: {project_path}")
    if project_path.is_dir():
        reina = ReinaColmena.from_project_path(
            project_path,
            incluir_dinamicas=args.incluir_dinamicas,
        )
        result = reina.evaluar()
        reporte = render_project_report(result)
        if args.auto_fix:
            reporte += (
                "\n\nAUTO-FIX: para carpetas de proyecto, esta corrida deja propuestas "
                "seguras y trazables en el reporte. No aplica cambios destructivos ni despliega."
            )
        print(reporte)
        txt_path, json_path = write_project_outputs(result)
        print(f"\nReporte guardado en: {txt_path}")
        print(f"JSON guardado en: {json_path}")
        return

    data = json.loads(project_path.read_text(encoding="utf-8-sig"))
    componentes = [Componente(**c) for c in data.get("componentes", [])]
    reina = ReinaColmena(
        data.get("project_id", "proyecto"),
        componentes,
        incluir_dinamicas=args.incluir_dinamicas,
    )

    if args.auto_fix:
        result = reina.evaluar_y_arreglar(
            project_file=project_path,
            max_iteraciones=args.max_iterations,
            min_confidence=args.min_confidence,
            apply_changes=not args.no_apply,
            git=not args.no_git,
            repo_root=Path.cwd(),
        )
        reporte = render_auto_fix_agent_report(result)
        filename = f"colmena_autofix_{result.project_id}.txt"
    else:
        result = reina.evaluar()
        reporte = render_colmena_report(result)
        filename = f"colmena_{result.project_id}.txt"

    print(reporte)
    out = Path("outputs")
    out.mkdir(exist_ok=True)
    path = out / filename
    path.write_text(reporte, encoding="utf-8")
    print(f"\nReporte guardado en: {path}")


if __name__ == "__main__":
    main()
