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
    ReinaColmena,
    apply_approved_patches,
    build_approval_manifest,
    build_patch_approval_report,
    export_patch_plan_items,
    parse_legacy_project_file,
    render_auto_fix_agent_report,
    render_colmena_report,
    render_patch_apply_report,
    render_patch_approval_report,
    render_patch_plan_report,
    render_project_report,
    render_repair_report,
    render_self_heal_report,
    run_self_heal,
    validate_approval_file,
    write_approval_manifest,
    write_patch_apply_report,
    write_patch_approval_report,
    write_patch_plan_outputs,
    write_project_outputs,
    write_self_heal_report,
    run_project_repair_loop,
)
from juez.colmena.models import RepairLoopConfig
from juez.colmena.patch_planner import build_patch_plan


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="juez")
    sub = parser.add_subparsers(dest="command", required=True)

    colmena = sub.add_parser("colmena", help="Evalua un proyecto con La Colmena")
    colmena.add_argument("--project", required=True, help="JSON del proyecto o carpeta a evaluar")
    colmena.add_argument("--incluir-dinamicas", action="store_true", help="Corre obreras dinamicas")
    colmena.add_argument("--auto-fix", action="store_true", help="Evalua, aplica fixes, re-evalua e itera")
    colmena.add_argument("--cases", type=int, default=10, help="Casos sinteticos para repair loop")
    colmena.add_argument("--max-iterations", type=int, default=5, help="Maximo de iteraciones de Auto-Fix")
    colmena.add_argument("--min-confidence", type=float, default=0.80, help="Confianza minima para aplicar fixes")
    colmena.add_argument(
        "--repair-mode",
        choices=["dry-run", "proposal-only", "apply-safe", "autonomous"],
        default=None,
        help="Activa repair loop para carpetas. apply-safe aun se ejecuta como propuesta segura.",
    )
    colmena.add_argument(
        "--generate-diffs",
        action="store_true",
        help="Genera previews de patches seguros desde el repair loop. No aplica cambios.",
    )
    colmena.add_argument(
        "--export-patches",
        action="store_true",
        help="Exporta diffs seguros a outputs/patches y genera manifiesto de aprobacion.",
    )
    colmena.add_argument("--approval-file", help="Valida un manifiesto de aprobacion JSON sin aplicar patches.")
    colmena.add_argument(
        "--apply-approved-patches",
        action="store_true",
        help="Aplica solo patches aprobados que crean archivos nuevos permitidos.",
    )
    colmena.add_argument("--no-apply", action="store_true", help="Re-evalua en memoria, sin escribir cambios")
    colmena.add_argument("--no-git", action="store_true", help="No crear backup branch ni commits automaticos")
    colmena.add_argument("--max-lines-per-fix", type=int, default=40, help="Maximo de lineas por fix autonomo")
    colmena.add_argument("--fast-reeval", action="store_true", help="Reserva para reevaluacion selectiva")
    colmena.add_argument(
        "--enable-generic-fixer",
        action="store_true",
        help="Activa el ciclo generar-probar-reintentar en sandbox para hallazgos sin fixer especifico.",
    )
    colmena.add_argument(
        "--max-attempts-per-finding",
        type=int,
        default=3,
        help="Intentos del fixer generico por hallazgo (techo absoluto 5).",
    )
    colmena.add_argument(
        "--max-findings-per-run",
        type=int,
        default=None,
        help="Si se pasa, sobreescribe --max-iterations para esta corrida.",
    )
    colmena.add_argument(
        "--time-limit-per-finding",
        type=int,
        default=10,
        help="Minutos maximos que el fixer generico puede gastar por hallazgo.",
    )

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
    if not project_path.exists():
        raise SystemExit(f"El proyecto no existe: {project_path}")
    if project_path.is_dir():
        if args.repair_mode == "autonomous":
            effective_max_iterations = (
                args.max_findings_per_run if args.max_findings_per_run is not None else args.max_iterations
            )
            result = write_self_heal_report(
                run_self_heal(
                    project_path,
                    min_confidence=args.min_confidence,
                    max_iterations=effective_max_iterations,
                    max_lines_per_fix=args.max_lines_per_fix,
                    fast_reeval=args.fast_reeval,
                    enable_generic_fixer=args.enable_generic_fixer,
                    max_attempts_per_finding=args.max_attempts_per_finding,
                    time_limit_per_finding_min=args.time_limit_per_finding,
                )
            )
            print(render_self_heal_report(result))
            print(f"\nSelf-heal report guardado en: {result.txt_report_path}")
            print(f"Self-heal JSON guardado en: {result.json_report_path}")
            print(f"Audit log guardado en: {result.audit_log_path}")
            return

        if args.apply_approved_patches:
            if not args.approval_file:
                raise SystemExit("--apply-approved-patches requiere --approval-file")
            if not Path(args.approval_file).exists():
                raise SystemExit(f"El approval file no existe: {args.approval_file}")
            apply_result = write_patch_apply_report(
                apply_approved_patches(
                    project_path=project_path,
                    approval_file=args.approval_file,
                )
            )
            print(render_patch_apply_report(apply_result))
            print(f"\nApply report guardado en: {apply_result.txt_report_path}")
            print(f"Apply report JSON guardado en: {apply_result.json_report_path}")
            print(f"Audit log guardado en: {apply_result.audit_log_path}")
            return

        if args.approval_file and not (args.repair_mode or args.generate_diffs or args.export_patches):
            validation = validate_approval_file(args.approval_file)
            approval_report = build_patch_approval_report(
                patch_plan=None,
                export_result=None,
                manifest=None,
                validation=validation,
                approval_file=args.approval_file,
                export_patches=False,
            )
            approval_report.project_path = str(project_path.resolve())
            approval_report = write_patch_approval_report(approval_report)
            print(render_patch_approval_report(approval_report))
            print(f"\nApproval report guardado en: {approval_report.txt_report_path}")
            print(f"Approval report JSON guardado en: {approval_report.json_report_path}")
            return

        if args.repair_mode or args.generate_diffs or args.export_patches or args.approval_file:
            repair_mode = args.repair_mode or "proposal-only"
            result = run_project_repair_loop(
                project_path,
                RepairLoopConfig(
                    cases_count=args.cases,
                    max_iterations=args.max_iterations,
                    repair_mode=repair_mode,
                ),
            )
            print(render_repair_report(result))
            print(f"\nReporte guardado en: {result.txt_report_path}")
            print(f"JSON guardado en: {result.json_report_path}")
            if args.generate_diffs or args.export_patches or args.approval_file:
                patch_plan = write_patch_plan_outputs(build_patch_plan(result, mode=repair_mode))
                print("\n" + render_patch_plan_report(patch_plan))
                print(f"\nPatch plan guardado en: {patch_plan.txt_report_path}")
                print(f"Patch plan JSON guardado en: {patch_plan.json_report_path}")
                if args.export_patches:
                    export_result = export_patch_plan_items(
                        patch_plan.items,
                        project_path=project_path,
                    )
                    manifest = write_approval_manifest(build_approval_manifest(export_result))
                    validation = validate_approval_file(args.approval_file) if args.approval_file else None
                    approval_report = write_patch_approval_report(
                        build_patch_approval_report(
                            patch_plan=patch_plan,
                            export_result=export_result,
                            manifest=manifest,
                            validation=validation,
                            approval_file=args.approval_file,
                            export_patches=args.export_patches,
                        )
                    )
                    print("\n" + render_patch_approval_report(approval_report))
                    print(f"\nApproval manifest guardado en: {manifest.manifest_path}")
                    print(f"Approval report guardado en: {approval_report.txt_report_path}")
                    print(f"Approval report JSON guardado en: {approval_report.json_report_path}")
                elif args.approval_file:
                    validation = validate_approval_file(args.approval_file)
                    approval_report = write_patch_approval_report(
                        build_patch_approval_report(
                            patch_plan=patch_plan,
                            export_result=None,
                            manifest=None,
                            validation=validation,
                            approval_file=args.approval_file,
                            export_patches=False,
                        )
                    )
                    print("\n" + render_patch_approval_report(approval_report))
                    print(f"\nApproval report guardado en: {approval_report.txt_report_path}")
                    print(f"Approval report JSON guardado en: {approval_report.json_report_path}")
            return
        reina = ReinaColmena.from_project_path(
            project_path,
            incluir_dinamicas=args.incluir_dinamicas,
        )
        result = reina.evaluar()
        report_txt = render_project_report(result)
        if args.auto_fix:
            report_txt += (
                "\n\nAUTO-FIX: para carpetas de proyecto, esta corrida deja propuestas "
                "seguras y trazables en el reporte. No aplica cambios destructivos ni despliega."
            )
        print(report_txt)
        txt_path, json_path = write_project_outputs(result)
        print(f"\nReporte guardado en: {txt_path}")
        print(f"JSON guardado en: {json_path}")
        return

    data = json.loads(project_path.read_text(encoding="utf-8-sig"))
    componentes = parse_legacy_project_file(data, project_path.stem)
    if not componentes and "componentes" not in data:
        raise SystemExit(
            f"No se detectaron componentes evaluables en {project_path}. "
            "Este archivo no tiene 'componentes' (formato legacy) ni 'nodes'+'connections' "
            "(export de flujo n8n). Nada se evaluo -- no interpretes la ausencia de "
            "hallazgos como un proyecto perfecto. Usa una carpeta de proyecto con --project "
            "para el escaneo completo, o revisa el formato del archivo."
        )
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
