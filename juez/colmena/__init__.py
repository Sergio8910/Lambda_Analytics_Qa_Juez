"""La Colmena — evaluación de un PROYECTO completo (varios agentes + flujos).

No reescribe el Juez: orquesta en paralelo las capacidades que ya existen
(seguridad de tools, análisis n8n, objetivos, evaluación de prompts) sobre cada
componente del proyecto y consolida los hallazgos en un reporte único.

Las obreras estáticas/sintéticas (seguras, sin costo) corren por defecto. Las
dinámicas (performance, adversarial, edge-cases) disparan/cuestan tokens y son
opt-in — se listan como "no ejecutadas" salvo que se activen.
"""
from .auto_fix_agent import AutoFixAgentResult, render_auto_fix_agent_report, run_auto_fix_agent
from .colmena import ColmenaResult, Componente, render_colmena_report, run_colmena
from .models import NormalizedFinding, ProjectEvaluationReport, ProjectInventory
from .project_evaluator import evaluate_project_path, render_project_report, write_project_outputs
from .reina import ReinaColmena

__all__ = [
    "AutoFixAgentResult",
    "ColmenaResult",
    "Componente",
    "NormalizedFinding",
    "ProjectEvaluationReport",
    "ProjectInventory",
    "ReinaColmena",
    "evaluate_project_path",
    "render_auto_fix_agent_report",
    "render_colmena_report",
    "render_project_report",
    "run_auto_fix_agent",
    "run_colmena",
    "write_project_outputs",
]
