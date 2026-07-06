"""Clasificador simple y trazable de proyectos para La Colmena."""
from __future__ import annotations

from .models import ProjectClassification, ProjectInventory


def classify_project(inventory: ProjectInventory) -> ProjectClassification:
    counts = inventory.detected_assets
    frameworks = set(inventory.frameworks)
    reasons: list[str] = []

    has_api = counts.get("apis", 0) > 0 or {"fastapi", "flask", "django", "node_api"} & frameworks
    has_agents = counts.get("agents", 0) > 0 or counts.get("prompts", 0) > 0
    has_workflows = counts.get("workflows", 0) > 0
    has_frontend = "frontend" in frameworks

    if "fastapi" in frameworks:
        reasons.append("Se detectaron imports/decoradores de FastAPI.")
    if has_api:
        reasons.append("Se detectaron endpoints o framework API.")
    if has_agents:
        reasons.append("Se detectaron prompts o archivos con patrones de agente.")
    if has_workflows:
        reasons.append("Se detectaron exports n8n con nodes/connections.")
    if has_frontend:
        reasons.append("Se detectaron dependencias frontend en package.json.")
    if "mcp" in frameworks or counts.get("mcp_config", 0):
        reasons.append("Se detectaron patrones/configuracion MCP.")
        return ProjectClassification(project_type="mcp_server", confidence=0.82, reasons=reasons)
    if has_api and "fastapi" in frameworks and has_agents:
        return ProjectClassification(project_type="mixed_ai_project", confidence=0.90, reasons=reasons)
    if has_api and "fastapi" in frameworks:
        return ProjectClassification(project_type="fastapi_project", confidence=0.88, reasons=reasons)
    if has_frontend and has_api:
        return ProjectClassification(project_type="frontend_backend_project", confidence=0.84, reasons=reasons)
    if has_workflows and has_agents:
        return ProjectClassification(project_type="multiagent_project", confidence=0.78, reasons=reasons)
    if has_workflows:
        return ProjectClassification(project_type="n8n_workflow_project", confidence=0.82, reasons=reasons)
    if has_agents:
        project_type = "multiagent_project" if counts.get("agents", 0) > 1 else "agent_only"
        return ProjectClassification(project_type=project_type, confidence=0.76, reasons=reasons)
    if has_api:
        return ProjectClassification(project_type="python_api_project", confidence=0.72, reasons=reasons)
    if counts.get("script", 0) or counts.get("python_file", 0):
        reasons.append("Se detectaron scripts/codigo Python sin framework dominante.")
        return ProjectClassification(project_type="automation_project", confidence=0.55, reasons=reasons)
    return ProjectClassification(
        project_type="unknown_project",
        confidence=0.20,
        reasons=reasons or ["No hubo senales suficientes para clasificar con confianza."],
    )
