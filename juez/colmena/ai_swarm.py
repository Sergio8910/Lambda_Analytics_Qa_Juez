"""Enjambre colaborativo: especialistas de IA y una Reina sintetizadora."""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from juez.llm_client import make_chat_client
from juez.swarm_context import SwarmContextRegistry, activate_bee


@dataclass(frozen=True)
class AIWorkerSpec:
    bee_id: str
    title: str
    mission: str
    categories: tuple[str, ...]
    file_hints: tuple[str, ...] = ()


_SPECS = (
    AIWorkerSpec(
        "guardiana_seguridad", "Guardiana de seguridad",
        "Busca exposición de secretos, autenticación débil, inyección, abuso de herramientas, SSRF y escalamiento de privilegios.",
        ("security",), ("auth", "security", ".env", "secret", "config", "api"),
    ),
    AIWorkerSpec(
        "inspectora_api", "Inspectora de APIs",
        "Revisa contratos HTTP, validación, errores, autenticación, asincronía, idempotencia y compatibilidad entre consumidores.",
        ("api", "integration"), ("api", "route", "router", "endpoint", "schema"),
    ),
    AIWorkerSpec(
        "inspectora_workflow", "Inspectora de workflows",
        "Revisa grafos n8n, ramas, triggers, nodos huérfanos, contratos de entrada/salida, reintentos y manejo de fallos.",
        ("workflow", "integration"), ("workflow", "n8n", ".json", "webhook"),
    ),
    AIWorkerSpec(
        "inspectora_codigo", "Inspectora de código",
        "Busca defectos lógicos, estados inconsistentes, concurrencia insegura, excepciones ocultas y deuda mantenible.",
        ("maintainability", "architecture"), (".py", "code", "service", "client"),
    ),
    AIWorkerSpec(
        "inspectora_prompt", "Inspectora de prompts y agentes",
        "Evalúa instrucciones, propósito, variables, guardrails, uso de herramientas, recuperación y resistencia a prompt injection.",
        ("agent", "prompt"), ("prompt", "agent", "system", ".json", ".txt"),
    ),
    AIWorkerSpec(
        "arquitecta", "Arquitecta",
        "Evalúa límites de módulos, responsabilidades, dependencias, persistencia, escalabilidad y puntos únicos de fallo.",
        ("architecture",), ("architecture", "main", "service", "docker", "compose"),
    ),
    AIWorkerSpec(
        "documentalista", "Documentalista",
        "Contrasta comportamiento, configuración y operación con la documentación; identifica ambigüedad o contenido obsoleto.",
        ("documentation",), ("readme", "doc", ".md", ".txt"),
    ),
    AIWorkerSpec(
        "desplegadora", "Especialista de despliegue",
        "Revisa configuración, variables, contenedores, servicios, healthchecks, observabilidad, rollback y recuperación.",
        ("deployment",), ("docker", "compose", "service", "deploy", ".yml", ".yaml"),
    ),
    AIWorkerSpec(
        "tester", "Especialista de pruebas",
        "Busca huecos de pruebas, contratos no cubiertos, fixtures irreales, falsos positivos y caminos críticos sin verificación.",
        ("testing",), ("test", "spec", "fixture", "pytest"),
    ),
    AIWorkerSpec(
        "negocio", "Especialista de reglas de negocio",
        "Comprueba que objetivos, reglas declaradas, estados y decisiones del sistema estén representados y sean verificables.",
        ("business_rule",), ("regla", "rule", "objective", "objetivo", "business"),
    ),
    AIWorkerSpec(
        "exploradora", "Exploradora adversarial",
        "Cuestiona supuestos, intenta refutar conclusiones favorables y busca escenarios adversariales omitidos.",
        ("security", "agent", "workflow"), ("prompt", "agent", "workflow", "security"),
    ),
    AIWorkerSpec(
        "ninera", "Niñera de casos límite",
        "Busca entradas vacías, enormes, ambiguas, repetidas, fuera de orden o parcialmente válidas y analiza degradación.",
        ("api", "workflow", "agent", "reliability"), ("api", "workflow", "agent", "schema"),
    ),
    AIWorkerSpec(
        "rendimiento", "Especialista de rendimiento",
        "Analiza latencia, llamadas seriales, polling, reintentos, memoria, tamaños de entrada y riesgos de saturación.",
        ("performance", "architecture"), ("performance", "timeout", "retry", "poll", "worker"),
    ),
)

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password|authorization)\s*[:=]\s*['\"]?([^\s,'\"]+)"),
    re.compile(r"\b(?:sk|ordo_sk|sk-ant)-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
)


def _redact(value: str) -> str:
    text = value
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]" if m.lastindex and m.lastindex >= 1 else "[REDACTED]", text)
    return text


def _finding_dict(finding: Any) -> dict[str, Any]:
    if hasattr(finding, "model_dump"):
        return finding.model_dump()
    if isinstance(finding, dict):
        return dict(finding)
    return {"description": str(finding)}


def _evidence_for(spec: AIWorkerSpec, findings: Iterable[Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for finding in findings:
        data = _finding_dict(finding)
        category = str(data.get("category") or data.get("categoria") or "")
        source = str(data.get("source") or data.get("obrera") or "")
        if category not in spec.categories and not any(hint in source.lower() for hint in spec.file_hints):
            continue
        selected.append({
            "severity": data.get("severity") or data.get("severidad"),
            "category": category,
            "title": data.get("title") or data.get("descripcion"),
            "description": _redact(str(data.get("description") or ""))[:800],
            "file": data.get("file") or data.get("ubicacion"),
            "recommendation": _redact(str(data.get("recommendation") or data.get("accion") or ""))[:500],
            "source": source,
        })
        if len(selected) >= 30:
            break
    return selected


def _asset_matches(spec: AIWorkerSpec, path: str, kind: str) -> bool:
    target = f"{path} {kind}".lower()
    return any(hint.lower() in target for hint in spec.file_hints)


def _source_context(root: Path, inventory: Any, spec: AIWorkerSpec) -> list[dict[str, str]]:
    if os.getenv("JUEZ_AI_SWARM_INCLUDE_SOURCE", "true").lower() != "true":
        return []
    total_limit = int(os.getenv("JUEZ_AI_SWARM_MAX_SOURCE_CHARS", "12000"))
    per_file = int(os.getenv("JUEZ_AI_SWARM_MAX_FILE_CHARS", "2500"))
    used = 0
    excerpts: list[dict[str, str]] = []
    for asset in getattr(inventory, "assets", []) or []:
        path_text = str(getattr(asset, "path", ""))
        kind = str(getattr(asset, "kind", ""))
        if not path_text or not _asset_matches(spec, path_text, kind):
            continue
        path = (root / path_text).resolve()
        try:
            if not path.is_file() or root not in path.parents:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        remaining = total_limit - used
        if remaining <= 0:
            break
        excerpt = _redact(text[: min(per_file, remaining)])
        excerpts.append({"path": path_text, "kind": kind, "excerpt": excerpt})
        used += len(excerpt)
        if len(excerpts) >= 8:
            break
    return excerpts


def _inventory_summary(inventory: Any) -> dict[str, Any]:
    return {
        "frameworks": list(getattr(inventory, "frameworks", []) or []),
        "languages": list(getattr(inventory, "languages", []) or []),
        "detected_assets": dict(getattr(inventory, "detected_assets", {}) or {}),
        "files_total": len(getattr(inventory, "assets", []) or []),
    }


def _safe_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"summary": str(data)}
    except Exception:
        return {"status": "invalid_response", "summary": text[:1000], "findings": []}


def _run_specialist(
    spec: AIWorkerSpec,
    *,
    project_id: str,
    root: Path,
    inventory: Any,
    deterministic_findings: list[Any],
    registry: SwarmContextRegistry,
) -> dict[str, Any]:
    evidence = _evidence_for(spec, deterministic_findings)
    sources = _source_context(root, inventory, spec)
    payload = {
        "project_id": project_id,
        "inventory": _inventory_summary(inventory),
        "deterministic_evidence": evidence,
        "relevant_source_excerpts": sources,
    }
    system = (
        f"Tu especialidad en esta revisión es: {spec.title}. {spec.mission} "
        "Trabajas como una especialista dentro de una colmena: no intentes cubrir "
        "las áreas de las otras abejas. Fundamenta todo en la evidencia recibida, "
        "distingue hechos de inferencias y no inventes archivos ni comportamientos. "
        "Responde exclusivamente JSON."
    )
    schema = (
        '{"bee_id":"...", "status":"completed", "summary":"...", '
        '"confidence":0.0, "findings":[{"severity":"critical|high|medium|low|info",'
        '"category":"...", "title":"...", "evidence":"...", "recommendation":"..."}],'
        '"dependencies":["bee_id"], "questions_for_queen":["..."]}'
    )
    user = (
        f"Analiza el siguiente contexto especializado.\n\n{json.dumps(payload, ensure_ascii=False)}"
        f"\n\nContrato de salida: {schema}"
    )
    with activate_bee(
        registry,
        bee_id=spec.bee_id,
        role=spec.title,
        component=project_id,
    ) as bee_context:
        try:
            client = make_chat_client(timeout=45, max_retries=2)
            response = client.chat.completions.create(
                model=os.getenv("JUDGE_MODEL", "juez"),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            report = _safe_json(response.choices[0].message.content or "{}")
            # La identidad la controla la Colmena, no el texto generado por el
            # modelo. Esto mantiene estable el tablero y las dependencias.
            report["bee_id"] = spec.bee_id
            report["status"] = "completed"
            report.setdefault("summary", "")
            report.setdefault("confidence", 0.0)
            report.setdefault("findings", [])
            report.setdefault("dependencies", [])
            report.setdefault("questions_for_queen", [])
            if not isinstance(report["findings"], list):
                report["findings"] = []
            bee_context.record_findings(report["findings"])
            return report
        except Exception as exc:
            bee_context.fail(exc)
            return {
                "bee_id": spec.bee_id,
                "status": "failed",
                "summary": "",
                "confidence": 0.0,
                "findings": [],
                "dependencies": [],
                "questions_for_queen": [],
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }


def _run_queen(
    project_id: str,
    reports: list[dict[str, Any]],
    registry: SwarmContextRegistry,
) -> dict[str, Any]:
    board = [{
        "bee_id": report.get("bee_id"),
        "status": report.get("status"),
        "summary": report.get("summary"),
        "confidence": report.get("confidence"),
        "findings": report.get("findings", [])[:12],
        "dependencies": report.get("dependencies", []),
        "questions_for_queen": report.get("questions_for_queen", []),
    } for report in reports]
    system = (
        "Eres la Reina coordinadora de una revisión multiagente. Recibes informes "
        "especializados, no el proyecto completo. Debes encontrar consenso, "
        "contradicciones, dependencias y huecos de cobertura. No inventes evidencia "
        "ni conviertas una opinión aislada en consenso. Responde exclusivamente JSON."
    )
    schema = (
        '{"summary":"...", "decision":"ready|observations|not_ready|inconclusive",'
        '"consensus_findings":[{"severity":"critical|high|medium|low|info",'
        '"category":"...", "title":"...", "evidence":"...", "recommendation":"...",'
        '"supporting_bees":["..."], "confidence":0.0}],'
        '"conflicts":[{"topic":"...", "bees":["..."], "resolution":"..."}],'
        '"coverage_gaps":["..."], "priorities":["..."]}'
    )
    with activate_bee(
        registry,
        bee_id="reina",
        role="Reina coordinadora",
        component=project_id,
    ) as queen_context:
        try:
            client = make_chat_client(timeout=60, max_retries=2)
            response = client.chat.completions.create(
                model=os.getenv("JUDGE_MODEL", "juez"),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": (
                        f"TABLERO DE LA COLMENA:\n{json.dumps(board, ensure_ascii=False)}"
                        f"\n\nContrato de salida: {schema}"
                    )},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            result = _safe_json(response.choices[0].message.content or "{}")
            result.setdefault("summary", "")
            result.setdefault("decision", "inconclusive")
            result.setdefault("consensus_findings", [])
            result.setdefault("conflicts", [])
            result.setdefault("coverage_gaps", [])
            result.setdefault("priorities", [])
            queen_context.record_findings(result["consensus_findings"])
            return result
        except Exception as exc:
            queen_context.fail(exc)
            return {
                "summary": "",
                "decision": "inconclusive",
                "consensus_findings": [],
                "conflicts": [],
                "coverage_gaps": ["La Reina no pudo consolidar los informes"],
                "priorities": [],
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }


def run_ai_swarm(
    *,
    project_id: str,
    root: Path,
    inventory: Any,
    deterministic_findings: list[Any],
    registry: SwarmContextRegistry,
) -> dict[str, Any]:
    """Ejecuta especialistas en paralelo y entrega sus informes a la Reina."""
    max_workers = max(1, int(os.getenv("JUEZ_AI_SWARM_MAX_WORKERS", "4")))
    reports: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(_SPECS))) as executor:
        futures = {
            executor.submit(
                _run_specialist,
                spec,
                project_id=project_id,
                root=root,
                inventory=inventory,
                deterministic_findings=deterministic_findings,
                registry=registry,
            ): spec
            for spec in _SPECS
        }
        for future in as_completed(futures):
            reports.append(future.result())
    reports.sort(key=lambda item: str(item.get("bee_id")))
    queen = _run_queen(project_id, reports, registry)
    return {
        "enabled": True,
        "collaboration_model": "specialists_to_shared_board_to_ai_queen",
        "specialists": reports,
        "queen": queen,
        "context_registry": registry.report(),
    }
