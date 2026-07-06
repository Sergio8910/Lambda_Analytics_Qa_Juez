"""La Colmena — evaluación de un PROYECTO completo (varios agentes + flujos).

No reescribe el Juez: orquesta en paralelo las capacidades que ya existen
(seguridad de tools, análisis n8n, objetivos, evaluación de prompts) sobre cada
componente del proyecto y consolida los hallazgos en un reporte único.

Las obreras estáticas/sintéticas (seguras, sin costo) corren por defecto. Las
dinámicas (performance, adversarial, edge-cases) disparan/cuestan tokens y son
opt-in — se listan como "no ejecutadas" salvo que se activen.
"""
from .colmena import ColmenaResult, run_colmena, render_colmena_report

__all__ = ["ColmenaResult", "run_colmena", "render_colmena_report"]
