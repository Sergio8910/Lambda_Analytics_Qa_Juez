"""CLI unificado del Juez.

Mantiene los evaluadores historicos como fallback y expone La Colmena como flujo
principal para proyectos completos.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from juez.colmena import (
    Componente,
    ReinaColmena,
    render_auto_fix_agent_report,
    render_colmena_report,
)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="juez")
    sub = parser.add_subparsers(dest="command", required=True)

    colmena = sub.add_parser("colmena", help="Evalua un proyecto con La Colmena")
    colmena.add_argument("--project", required=True, help="JSON del proyecto")
    colmena.add_argument("--incluir-dinamicas", action="store_true", help="Corre obreras dinamicas")
    colmena.add_argument("--auto-fix", action="store_true", help="Evalua, aplica fixes, re-evalua e itera")
    colmena.add_argument("--max-iterations", type=int, default=5, help="Maximo de iteraciones de Auto-Fix")
    colmena.add_argument("--min-confidence", type=float, default=0.80, help="Confianza minima para aplicar fixes")
    colmena.add_argument("--no-apply", action="store_true", help="Re-evalua en memoria, sin escribir cambios")
    colmena.add_argument("--no-git", action="store_true", help="No crear backup branch ni commits automaticos")

    sub.add_parser("evaluate-agent", help="Fallback historico: use juez/evaluar_elevenlabs.py")
    sub.add_parser("evaluate-workflow", help="Fallback historico: use juez/evaluar_n8n.py")

    args, unknown = parser.parse_known_args()
    if args.command == "colmena":
        _run_colmena(args)
        return
    if args.command == "evaluate-agent":
        raise SystemExit(
            "Fallback disponible con: python juez/evaluar_elevenlabs.py " + " ".join(unknown)
        )
    if args.command == "evaluate-workflow":
        raise SystemExit(
            "Fallback disponible con: python juez/evaluar_n8n.py " + " ".join(unknown)
        )


def _run_colmena(args: argparse.Namespace) -> None:
    project_path = Path(args.project)
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
        print(render_auto_fix_agent_report(result))
    else:
        print(render_colmena_report(reina.evaluar()))


if __name__ == "__main__":
    main()
