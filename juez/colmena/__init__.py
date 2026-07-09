"""La Colmena — evaluación de un PROYECTO completo (varios agentes + flujos).

No reescribe el Juez: orquesta en paralelo las capacidades que ya existen
(seguridad de tools, análisis n8n, objetivos, evaluación de prompts) sobre cada
componente del proyecto y consolida los hallazgos en un reporte único.

Las obreras estáticas/sintéticas (seguras, sin costo) corren por defecto. Las
dinámicas (performance, adversarial, edge-cases) disparan/cuestan tokens y son
opt-in — se listan como "no ejecutadas" salvo que se activen.
"""
from .apply_report import render_patch_apply_report, write_patch_apply_report
from .approval_manifest import build_approval_manifest, write_approval_manifest
from .approval_report import (
    build_patch_approval_report,
    render_patch_approval_report,
    write_patch_approval_report,
)
from .approval_validator import validate_approval_file
from .auto_fix_agent import AutoFixAgentResult, render_auto_fix_agent_report, run_auto_fix_agent
from .colmena import ColmenaResult, Componente, parse_legacy_project_file, render_colmena_report, run_colmena
from .iteration_loop import run_project_repair_loop
from .models import NormalizedFinding, ProjectEvaluationReport, ProjectInventory
from .patch_applier import apply_approved_patches
from .patch_exporter import export_patch_plan_items
from .patch_models import PatchPlan, PatchPlanItem
from .patch_report import render_patch_plan_report, write_patch_plan_outputs
from .project_evaluator import evaluate_project_path, render_project_report, write_project_outputs
from .reina import ReinaColmena
from .repair_report import render_repair_report
from .self_heal_agent import run_self_heal
from .self_heal_report import render_self_heal_report, write_self_heal_report

__all__ = [
    "AutoFixAgentResult",
    "ColmenaResult",
    "Componente",
    "NormalizedFinding",
    "ProjectEvaluationReport",
    "ProjectInventory",
    "PatchPlan",
    "PatchPlanItem",
    "ReinaColmena",
    "apply_approved_patches",
    "parse_legacy_project_file",
    "build_approval_manifest",
    "build_patch_approval_report",
    "evaluate_project_path",
    "export_patch_plan_items",
    "render_auto_fix_agent_report",
    "render_colmena_report",
    "render_patch_apply_report",
    "render_patch_approval_report",
    "render_patch_plan_report",
    "render_project_report",
    "render_repair_report",
    "render_self_heal_report",
    "run_project_repair_loop",
    "run_auto_fix_agent",
    "run_colmena",
    "run_self_heal",
    "validate_approval_file",
    "write_approval_manifest",
    "write_patch_apply_report",
    "write_patch_approval_report",
    "write_patch_plan_outputs",
    "write_project_outputs",
    "write_self_heal_report",
]
