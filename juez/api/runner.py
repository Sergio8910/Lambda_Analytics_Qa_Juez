"""Runners no-interactivos para los evaluadores del Juez.

Estos wrappers ejecutan exactamente la misma lÃ³gica que `evaluar_elevenlabs.py`,
`evaluar_n8n.py` y `evaluar_pipeline.py`, pero sin prompts interactivos.
Reciben todos los parÃ¡metros como argumentos y retornan un dict serializable.

La carga de los mÃ³dulos se hace dinÃ¡micamente con `importlib.util` para no
forzar refactor de los scripts existentes.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Ruta al root del proyecto (donde estÃ¡n evaluar_*.py)
_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# CARGA DINÃMICA DE MÃ“DULOS
# =============================================================================

_modules_cache: Dict[str, Any] = {}


def _load_module(name: str) -> Any:
    """Carga un mÃ³dulo evaluar_*.py por nombre, con cachÃ©."""
    if name in _modules_cache:
        return _modules_cache[name]
    path = _ROOT / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"No se encontrÃ³ {path}")
    # Asegurar que el root estÃ© en sys.path para imports relativos del mÃ³dulo
    root_str = str(_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    spec = _ilu.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo cargar spec para {path}")
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _modules_cache[name] = mod
    return mod


# =============================================================================
# UTILIDADES
# =============================================================================


def _progress_noop(step: str, percent: int = 0) -> None:
    pass


def _resolve_n8n_flow(
    flow_dict: Dict[str, Any],
    n8n_api_key: str,
    n8n_base_url: str,
) -> Tuple[Dict[str, Any], str, str]:
    """Resuelve una `N8nFlowSource` a (workflow_dict, webhook_url, nombre).

    Acepta JSON directo, URL completa, o ID. Si es URL/ID, descarga via API.
    """
    n8n_mod = _load_module("evaluar_pipeline")  # para usar las helpers privadas

    json_content = flow_dict.get("json_content")
    webhook_override = flow_dict.get("webhook_url") or ""

    # Caso 1: JSON ya viene en el request
    if json_content:
        wf = json_content if isinstance(json_content, dict) else json.loads(json_content)
        nombre = wf.get("name", "flujo-n8n")
        webhook = webhook_override
        if not webhook:
            base_url = n8n_base_url or os.getenv("N8N_BASE_URL", "")
            if base_url:
                try:
                    webhook = n8n_mod._extraer_webhook_url(wf, base_url)
                except Exception:
                    webhook = ""
        return wf, webhook, nombre

    # Caso 2: URL directa de webhook. No hay workflow JSON para descargar, pero
    # si el usuario guardo el agente como /webhook/... debe poder ejecutarse.
    url_o_id = flow_dict.get("url") or flow_dict.get("workflow_id")
    if not url_o_id:
        raise ValueError(
            "Cada flujo n8n debe traer 'json_content', 'url' o 'workflow_id'"
        )
    url_o_id = str(url_o_id).strip()
    parsed = urlparse(url_o_id)
    if parsed.scheme in {"http", "https"} and ("/webhook/" in parsed.path or "/webhook-test/" in parsed.path):
        path = parsed.path.split("/webhook/", 1)[-1].split("/webhook-test/", 1)[-1].strip("/")
        nombre = path or "webhook-n8n"
        wf = {
            "name": nombre,
            "nodes": [
                {
                    "name": "Webhook Entrada",
                    "type": "n8n-nodes-base.webhook",
                    "parameters": {"path": path},
                }
            ],
            "connections": {},
        }
        return wf, webhook_override or url_o_id, nombre

    # Caso 3: URL de workflow o ID â€” descargar via API
    base_url_extraida, wf_id = n8n_mod._parsear_url_workflow(url_o_id)
    base_url = base_url_extraida or n8n_base_url or os.getenv("N8N_BASE_URL", "")
    api_key = n8n_api_key or os.getenv("N8N_API_KEY", "")

    if not base_url:
        raise ValueError(
            "No se pudo determinar la URL base de n8n. Pasa la URL completa "
            "(https://tu-n8n.com/workflow/ID) o configura n8n_base_url."
        )
    if not api_key:
        raise ValueError("Falta n8n_api_key (o N8N_API_KEY en el entorno)")

    wf = n8n_mod._descargar_workflow_n8n(base_url, api_key, wf_id)
    webhook = webhook_override or n8n_mod._extraer_webhook_url(wf, base_url)
    nombre = wf.get("name", wf_id)
    return wf, webhook, nombre


def _setear_env_temporal(
    openai_key: str = "",
    elevenlabs_key: str = "",
    n8n_api_key: str = "",
    n8n_base_url: str = "",
) -> Dict[str, Optional[str]]:
    """Setea variables de entorno temporales y retorna los valores previos
    para poder restaurarlos.

    Necesario porque los evaluadores leen `os.getenv(...)` internamente.
    """
    previo: Dict[str, Optional[str]] = {}
    mapping = {
        "OPENAI_API_KEY":    openai_key,
        "ELEVENLABS_API_KEY": elevenlabs_key,
        "N8N_API_KEY":       n8n_api_key,
        "N8N_BASE_URL":      n8n_base_url,
    }
    for key, val in mapping.items():
        if val:
            previo[key] = os.environ.get(key)
            os.environ[key] = val
    return previo


def _restaurar_env(previo: Dict[str, Optional[str]]) -> None:
    for key, val in previo.items():
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


def _strip_non_serializable(obj: Any) -> Any:
    """Convierte recursivamente objetos no-serializables a strings."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _strip_non_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_strip_non_serializable(v) for v in obj]
    # Dataclass o pydantic
    if hasattr(obj, "model_dump"):
        try:
            return _strip_non_serializable(obj.model_dump(mode="json"))
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return _strip_non_serializable(vars(obj))
        except Exception:
            pass
    return str(obj)


def _guardar_reporte_txt(nombre: str, contenido: str, kind: str) -> str:
    """Guarda el reporte en outputs/api_reports/ y retorna la ruta absoluta."""
    out_dir = _ROOT / "outputs" / "api_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    nombre_limpio = "".join(c for c in nombre if c.isalnum() or c in " _-").strip().replace(" ", "_")[:50]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{kind}_{nombre_limpio}_{ts}.txt"
    path.write_text(contenido, encoding="utf-8")
    return str(path)


def _sev_rank(sev: str) -> int:
    return {"CRITICO": 0, "ALTO": 1, "MEDIO": 2, "BAJO": 3}.get(str(sev).upper(), 4)


def _classify_problem(p: Dict[str, Any]) -> str:
    txt = " ".join(
        str(p.get(k, ""))
        for k in ("titulo", "tipo", "descripcion", "ubicacion", "origen", "nodo", "componente", "recomendacion")
    ).lower()
    if any(k in txt for k in ("seguridad", "auth", "token", "api key", "credential", "credencial", "secret", "ssrf", "inyeccion", "prompt injection")):
        return "seguridad"
    if any(k in txt for k in ("http", "webhook", "schema", "parser", "rag", "modelo", "timeout", "api", "nodo", "tool", "codigo", "javascript")):
        return "tecnico"
    return "funcional"


def _problem_to_item(p: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "severidad": str(p.get("severidad") or "INFO"),
        "titulo": str(p.get("titulo") or p.get("tipo") or p.get("nodo") or "Hallazgo"),
        "detalle": str(p.get("descripcion") or p.get("detalle") or "")[:700],
        "componente": str(p.get("ubicacion") or p.get("nodo") or p.get("componente") or ""),
        "recomendacion": str(p.get("recomendacion") or p.get("accion") or "")[:500],
    }


def _batch_focus_summary(batch_result: Any) -> Dict[str, Any]:
    if not batch_result:
        return {
            "ejecutadas": False,
            "total": 0,
            "aprobadas": 0,
            "fallidas": 0,
            "pass_rate": 0,
            "categorias": {},
            "fallos_representativos": [],
            "evidencia_tecnica": [],
        }
    failures = []
    evidence = []
    for r in getattr(batch_result, "results", []) or []:
        if not getattr(r, "passed", False) and len(failures) < 5:
            failures.append({
                "plan_id": getattr(r, "plan_id", ""),
                "categoria": getattr(r, "category", ""),
                "diagnostico": getattr(r, "diagnosis", ""),
                "score": round(float(getattr(r, "overall_score", 0.0)) * 100),
            })
        for tr in getattr(r, "turn_results", []) or []:
            dbg = getattr(tr, "transport_debug", None)
            if dbg and len(evidence) < 8:
                evidence.append({
                    "plan_id": getattr(r, "plan_id", ""),
                    "turno": getattr(tr, "turn_id", None),
                    "categoria": getattr(r, "category", ""),
                    "debug": _strip_non_serializable(dbg),
                })
    return {
        "ejecutadas": True,
        "total": int(getattr(batch_result, "total", 0)),
        "aprobadas": int(getattr(batch_result, "passed", 0)),
        "fallidas": int(getattr(batch_result, "failed", 0)),
        "pass_rate": round(float(getattr(batch_result, "pass_rate", 0.0)) * 100),
        "categorias": _strip_non_serializable(getattr(batch_result, "by_category", {})),
        "fallos_representativos": failures,
        "evidencia_tecnica": evidence,
    }


def _aggregate_pruebas_summary(nodos: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Suma los `batch_summary` de los nodos de un pipeline/proyecto en un unico
    resumen de pruebas dinamicas (total/aprobadas/fallidas/pass_rate + evidencia).

    Necesario porque `run_proyecto`/`run_pipeline` NO tienen un `batch_result`
    unico: cada nodo corrio su propio batch. Sin esto, el panel muestra 0 pruebas
    aunque las conversaciones si se ejecutaron nodo a nodo.
    """
    total = aprobadas = fallidas = 0
    ejecutadas = False
    categorias: Dict[str, Any] = {}
    fallos: List[Dict[str, Any]] = []
    evidencia: List[Dict[str, Any]] = []
    for n in nodos or []:
        if not isinstance(n, dict):
            continue
        bs = n.get("batch_summary") or {}
        if not isinstance(bs, dict):
            continue
        if bs.get("ejecutadas"):
            ejecutadas = True
        total += int(bs.get("total") or 0)
        aprobadas += int(bs.get("aprobadas") or 0)
        fallidas += int(bs.get("fallidas") or 0)
        for fr in (bs.get("fallos_representativos") or []):
            if len(fallos) < 5:
                fallos.append(fr)
        for ev in (bs.get("evidencia_tecnica") or []):
            if len(evidencia) < 8:
                evidencia.append(ev)
        cats = bs.get("categorias") or {}
        if isinstance(cats, dict):
            for k, v in cats.items():
                if isinstance(v, dict) and isinstance(categorias.get(k), dict):
                    for kk, vv in v.items():
                        if isinstance(vv, (int, float)):
                            categorias[k][kk] = categorias[k].get(kk, 0) + vv
                        else:
                            categorias[k][kk] = vv
                else:
                    categorias[k] = v
    pass_rate = round((aprobadas / total) * 100) if total else 0
    return {
        "ejecutadas": ejecutadas,
        "total": total,
        "aprobadas": aprobadas,
        "fallidas": fallidas,
        "pass_rate": pass_rate,
        "categorias": categorias,
        "fallos_representativos": fallos,
        "evidencia_tecnica": evidencia,
    }


def _build_informe_enfoques(
    *,
    kind: str,
    nombre: str,
    score_general: float,
    problemas: List[Dict[str, Any]],
    batch_result: Any = None,
    trigger: Optional[Dict[str, Any]] = None,
    webhook_status: str = "",
    nodos: Optional[List[Dict[str, Any]]] = None,
    flujos_fallidos: Optional[List[Dict[str, Any]]] = None,
    pruebas_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    problemas = [p for p in (problemas or []) if isinstance(p, dict)]
    problemas_sorted = sorted(problemas, key=lambda p: _sev_rank(str(p.get("severidad", ""))))
    por_enfoque = {"funcional": [], "tecnico": [], "seguridad": []}
    for p in problemas_sorted:
        por_enfoque[_classify_problem(p)].append(_problem_to_item(p))

    # `pruebas_summary` (agregado de varios nodos) tiene prioridad sobre un
    # unico `batch_result`. Asi el panel refleja las pruebas dinamicas reales
    # en run_proyecto/run_pipeline, donde no hay un batch_result monolitico.
    dyn = pruebas_summary if pruebas_summary is not None else _batch_focus_summary(batch_result)
    score = round(float(score_general or 0.0))
    estado = "saludable" if score >= 85 else "requiere ajustes" if score >= 70 else "critico"
    trigger = trigger or {}
    flujos_fallidos = flujos_fallidos or []
    nodos = nodos or []

    funcional_checks = [
        "Conversaciones happy path y de usuario cooperativo",
        "Recorrido completo del agente cuando hay herramientas o varios pasos",
        "Memoria de contexto entre turnos",
        "Manejo de datos incompletos, usuario agresivo y casos fuera de dominio",
    ]
    tecnico_checks = [
        "Estructura del flujo/agente, nodos, conexiones y salidas",
        "Disponibilidad del webhook o canal de ejecucion real",
        "Payload enviado y respuesta observada por turno",
        "Schemas, parsers, RAG, tools, timeouts y errores HTTP",
    ]
    seguridad_checks = [
        "Exposicion de credenciales o API keys",
        "Autenticacion de webhooks y herramientas",
        "Resistencia a manipulacion/prompt injection",
        "Riesgos de fuga de datos o acciones fuera de permiso",
    ]

    return {
        "version": "2026-07-09",
        "nombre": nombre,
        "tipo": kind,
        "score_general": score,
        "estado": estado,
        "resumen": (
            f"Se evaluo {nombre} con enfoque funcional, tecnico y de seguridad. "
            f"Score general: {score}/100. "
            f"Pruebas dinamicas: {'si' if dyn['ejecutadas'] else 'no'}"
            f"{' (' + str(dyn['aprobadas']) + '/' + str(dyn['total']) + ' aprobadas)' if dyn['ejecutadas'] else ''}."
        ),
        "funcional": {
            "estado": "bien" if not por_enfoque["funcional"] and score >= 80 else "revisar",
            "que_se_evaluo": funcional_checks,
            "hallazgos": por_enfoque["funcional"][:8],
            "pruebas": {
                "total": dyn["total"],
                "aprobadas": dyn["aprobadas"],
                "fallidas": dyn["fallidas"],
                "pass_rate": dyn["pass_rate"],
                "categorias": dyn["categorias"],
                "fallos_representativos": dyn["fallos_representativos"],
            },
        },
        "tecnico": {
            "estado": "bien" if not por_enfoque["tecnico"] and not flujos_fallidos else "revisar",
            "que_se_evaluo": tecnico_checks,
            "hallazgos": por_enfoque["tecnico"][:10],
            "trigger": _strip_non_serializable(trigger),
            "webhook_status": webhook_status,
            "nodos": _strip_non_serializable(nodos[:12]),
            "flujos_fallidos": _strip_non_serializable(flujos_fallidos),
            "evidencia": dyn["evidencia_tecnica"],
        },
        "seguridad": {
            "estado": "bien" if not por_enfoque["seguridad"] else "revisar",
            "que_se_evaluo": seguridad_checks,
            "hallazgos": por_enfoque["seguridad"][:10],
        },
        "recomendaciones_rapidas": _build_recomendaciones_rapidas(por_enfoque, dyn, flujos_fallidos),
    }


def _build_recomendaciones_rapidas(por_enfoque: Dict[str, List[Dict[str, Any]]], dyn: Dict[str, Any], flujos_fallidos: List[Dict[str, Any]]) -> List[str]:
    recs: List[str] = []
    if dyn.get("ejecutadas") and dyn.get("pass_rate", 100) < 80:
        recs.append("Revisar los turnos fallidos: el agente no esta completando consistentemente el flujo conversacional.")
    if dyn.get("evidencia_tecnica"):
        recs.append("Comparar el payload enviado por el Juez con los campos que el webhook espera en n8n.")
    if flujos_fallidos:
        recs.append("Resolver los flujos que no pudieron analizarse antes de confiar en el score global.")
    if por_enfoque.get("seguridad"):
        recs.append("Atender primero los hallazgos de seguridad: credenciales, autenticacion o manipulacion del agente.")
    if por_enfoque.get("tecnico"):
        recs.append("Corregir schemas, tools, webhooks o errores HTTP antes de repetir la prueba real.")
    if not recs:
        recs.append("Mantener monitoreo recurrente y repetir pruebas reales despues de cambios del agente.")
    return recs[:6]


def _render_informe_enfoques_txt(informe: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("")
    lines.append("=" * 80)
    lines.append("  INFORME POR ENFOQUES")
    lines.append("=" * 80)
    lines.append("")
    lines.append(f"  Resumen: {informe.get('resumen', '')}")
    for key, title in (("funcional", "FUNCIONAL"), ("tecnico", "TECNICO"), ("seguridad", "SEGURIDAD")):
        sec = informe.get(key, {}) or {}
        lines.append("")
        lines.append(f"  {title}")
        lines.append("  " + "-" * len(title))
        lines.append(f"  Estado: {sec.get('estado', 'sin dato')}")
        checks = sec.get("que_se_evaluo") or []
        if checks:
            lines.append("  Que se evaluo:")
            for c in checks:
                lines.append(f"    - {c}")
        hallazgos = sec.get("hallazgos") or []
        if hallazgos:
            lines.append("  Hallazgos principales:")
            for h in hallazgos[:6]:
                prefix = f"[{h.get('severidad', 'INFO')}] {h.get('titulo', 'Hallazgo')}"
                detail = h.get("detalle") or ""
                lines.append(f"    - {prefix}: {detail[:220]}")
        else:
            lines.append("  Hallazgos principales: sin hallazgos criticos en este enfoque.")
    recs = informe.get("recomendaciones_rapidas") or []
    if recs:
        lines.append("")
        lines.append("  RECOMENDACIONES RAPIDAS")
        lines.append("  -----------------------")
        for r in recs:
            lines.append(f"    - {r}")
    return "\n".join(lines)


# =============================================================================
# RUNNERS
# =============================================================================


def _run_elevenlabs_branch(
    branch_id: str,
    total_conversaciones: int,
    concurrencia: Optional[int],
    escenarios: List[str],
    openai_key: str,
    elevenlabs_key: str,
    n8n_api_key: str,
    n8n_base_url: str,
    include_n8n_flows: bool,
    progress_cb: Optional[Callable[[str, int], None]],
) -> Dict[str, Any]:
    """EvalÃºa un branch de ElevenLabs.

    Si include_n8n_flows=False: solo el agente bajo el branch (1 nodo).
    Si include_n8n_flows=True : pipeline = agente + flujos n8n llamados via tools.
    """
    from juez.api.elevenlabs_discovery import (
        resolve_branch, extract_outbound_urls, match_urls_to_n8n_flows,
    )

    progress = progress_cb or _progress_noop
    eleven_key = elevenlabs_key or os.getenv("ELEVENLABS_API_KEY", "")
    n8n_key = n8n_api_key or os.getenv("N8N_API_KEY", "")
    n8n_url = n8n_base_url or os.getenv("N8N_BASE_URL", "")

    progress("Resolviendo branch a agente padre", 5)
    branch_info = resolve_branch(branch_id, eleven_key)
    agent_id = branch_info["agent_id"]
    branch_name = branch_info["branch_name"]

    progress(f"Branch '{branch_name}' del agente {agent_id}", 10)

    if not include_n8n_flows:
        # Caso simple: solo el agente bajo este branch (no se descubre n8n)
        # Aprovechamos el flujo existente cambiando agent_id por el padre del branch
        # pero usando la config descargada con el branch_id (que sÃ­ refleja la rama).
        return _evaluar_agente_directo_con_config(
            agent_id=agent_id,
            agent_config=branch_info["agent_config"],
            branch_context={"branch_id": branch_id, "branch_name": branch_name},
            total_conversaciones=total_conversaciones,
            concurrencia=concurrencia,
            escenarios=escenarios,
            openai_key=openai_key,
            elevenlabs_key=elevenlabs_key,
            progress=progress,
        )

    # Caso pipeline: descubrir flujos n8n llamados via tools
    progress("Detectando URLs salientes del agente", 15)
    urls_salientes = extract_outbound_urls(branch_info["agent_config"], eleven_key)

    progress(f"Buscando flujos n8n matcheables ({len(urls_salientes)} URLs)", 20)
    discovery = match_urls_to_n8n_flows(urls_salientes, n8n_url, n8n_key)

    # Armar la lista de flujos n8n a evaluar a partir de los matches
    n8n_flows: List[Dict[str, Any]] = []
    seen_wf_ids = set()
    for m in discovery["matches"]:
        wf_id = m["workflow_id"]
        if wf_id in seen_wf_ids:
            continue
        seen_wf_ids.add(wf_id)
        n8n_flows.append({"workflow_id": wf_id})

    progress(f"Flujos n8n descubiertos: {len(n8n_flows)}", 25)

    # Delegar al runner de pipeline existente (que ya sabe hacer todo)
    result = run_pipeline(
        nombre=f"Branch {branch_name} ({agent_id})",
        eleven_ids=[agent_id],
        n8n_flows=n8n_flows,
        total_conversaciones=total_conversaciones,
        concurrencia=concurrencia or 3,
        escenarios=escenarios,
        openai_key=openai_key,
        elevenlabs_key=elevenlabs_key,
        n8n_api_key=n8n_key,
        n8n_base_url=n8n_url,
        progress_cb=progress,
    )

    # Enriquecer el resultado con info del branch + descubrimiento
    result["kind"] = "elevenlabs_branch"
    result["branch"] = {
        "branch_id": branch_id,
        "branch_name": branch_name,
        "agent_id": agent_id,
    }
    result["n8n_discovery"] = {
        "urls_salientes": urls_salientes,
        "matches": discovery["matches"],
        "sin_match": discovery["sin_match"],
        "externos": discovery["externos"],
    }
    return result


def _evaluar_agente_directo_con_config(
    agent_id: str,
    agent_config: Dict[str, Any],
    branch_context: Dict[str, str],
    total_conversaciones: int,
    concurrencia: Optional[int],
    escenarios: List[str],
    openai_key: str,
    elevenlabs_key: str,
    progress: Callable[[str, int], None],
) -> Dict[str, Any]:
    """EvalÃºa un agente cuando ya tenemos la config descargada (vÃ­a branch).

    Variante de run_elevenlabs_single que no re-descarga el agente sino que
    usa el agent_config ya obtenido del branch.
    """
    previo_env = _setear_env_temporal(openai_key=openai_key, elevenlabs_key=elevenlabs_key)
    try:
        mod = _load_module("evaluar_elevenlabs")
        eleven_key = elevenlabs_key or os.getenv("ELEVENLABS_API_KEY", "")
        nombre = agent_config.get("name", agent_id)

        progress(f"AnÃ¡lisis estÃ¡tico: {nombre} (branch {branch_context['branch_name']})", 30)
        analisis = mod.ElevenLabsAnalyzer(agent_config).analizar()
        # Inyectar contexto del branch en el anÃ¡lisis para que salga en el reporte
        analisis["branch_context"] = branch_context

        gpt_result: Dict[str, Any] = {}
        oai_key = openai_key or os.getenv("OPENAI_API_KEY", "")
        if oai_key:
            progress("AnÃ¡lisis profundo con GPT", 45)
            try:
                gpt_result = mod.analizar_con_gpt(analisis, nombre)
                analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
            except Exception:
                pass

        # Contra-agente
        batch_result = None
        reporte_ca = ""
        if total_conversaciones > 0 and oai_key:
            try:
                from juez.evaluation.contra_agente.generator import generar_batch as _gen
                from juez.evaluation.contra_agente.evaluator import TurnEvaluator as _TE
                from juez.evaluation.contra_agente.reporter import generar_reporte_batch as _rep
                from juez.evaluation.contra_agente.adapters.elevenlabs import ElevenLabsAdapter
                from juez.evaluation.contra_agente.worker import ConversationWorker
                from juez.evaluation.contra_agente.models import BatchResult
                import concurrent.futures

                progress(f"Generando {total_conversaciones} conversaciones", 60)
                conc = concurrencia or min(max(total_conversaciones // 4, 2), 8)
                batch = _gen(
                    analisis=analisis,
                    agent_name=nombre,
                    total=total_conversaciones,
                    concurrency=conc,
                    adapter="elevenlabs",
                    openai_key=oai_key,
                    escenarios_extra=escenarios,
                )
                evaluator = _TE(openai_key=oai_key)

                def _run_one(plan: Any) -> Any:
                    adapter = ElevenLabsAdapter(
                        agent_id=agent_id,
                        analisis=analisis,
                        openai_key=oai_key,
                        el_key=eleven_key,
                    )
                    return ConversationWorker(plan, adapter, evaluator, openai_key=oai_key).run()

                progress("Ejecutando conversaciones contra el agente", 75)
                results: List[Any] = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as ex:
                    futures = [ex.submit(_run_one, p) for p in batch.plans]
                    for fut in concurrent.futures.as_completed(futures):
                        try:
                            results.append(fut.result())
                        except Exception:
                            pass

                total = len(results)
                passed = sum(1 for r in results if r.passed)
                by_cat: Dict[str, Dict[str, Any]] = {}
                for r in results:
                    cat = r.category
                    by_cat.setdefault(cat, {"total": 0, "passed": 0, "pass_rate": 0.0})
                    by_cat[cat]["total"] += 1
                    if r.passed:
                        by_cat[cat]["passed"] += 1
                for cat in by_cat:
                    t = by_cat[cat]["total"]
                    by_cat[cat]["pass_rate"] = round(by_cat[cat]["passed"] / t, 3) if t else 0.0

                batch_result = BatchResult(
                    batch_id=batch.batch_id, agent_id=agent_id,
                    total=total, passed=passed, failed=total - passed,
                    pass_rate=round(passed / total, 3) if total else 0.0,
                    by_category=by_cat,
                    collapse_pattern={},
                    results=results,
                    recommendations=[],
                    scorecard={},
                )
                reporte_ca = _rep(batch_result, agent_name=nombre)
            except Exception as exc:
                reporte_ca = f"\n[CONTRA-AGENTE â€” Error: {exc}]\n"

        progress("Calculando scorecard", 90)
        scores = mod.calcular_scorecard(analisis, batch_result)
        progress("Generando reporte", 96)
        reporte = mod.generar_reporte(
            analisis, gpt_result, nombre, agent_id,
            juez_report=None, scores_precalculados=scores,
        )
        if reporte_ca:
            reporte = reporte + "\n\n" + reporte_ca

        reporte_path = _guardar_reporte_txt(
            f"branch_{branch_context['branch_name']}_{nombre}",
            reporte, "elevenlabs_branch",
        )

        return {
            "kind": "elevenlabs_branch",
            "nombre": nombre,
            "agent_id": agent_id,
            "branch": branch_context,
            "score_general": scores.get("score_general", 0.0),
            "scores": _strip_non_serializable(scores),
            "problemas": _strip_non_serializable(analisis.get("problemas", [])),
            "dynamic_tests_ran": batch_result is not None,
            "batch_summary": (
                {
                    "total": batch_result.total,
                    "passed": batch_result.passed,
                    "pass_rate": batch_result.pass_rate,
                    "by_category": batch_result.by_category,
                } if batch_result else None
            ),
            "reporte_txt": reporte,
            "reporte_path": reporte_path,
        }
    finally:
        _restaurar_env(previo_env)


def run_elevenlabs_single(
    agent_id: Optional[str] = None,
    total_conversaciones: int = 20,
    concurrencia: Optional[int] = None,
    escenarios: Optional[List[str]] = None,
    openai_key: str = "",
    elevenlabs_key: str = "",
    progress_cb: Optional[Callable[[str, int], None]] = None,
    # Nuevos parÃ¡metros (T-23):
    target_id: Optional[str] = None,
    include_n8n_flows: bool = True,
    n8n_api_key: str = "",
    n8n_base_url: str = "",
) -> Dict[str, Any]:
    """EvalÃºa un recurso de ElevenLabs.

    `target_id` (o `agent_id` como alias) puede ser:
      - agent_*    : se evalÃºa el agente directo (versiÃ³n live).
      - agtbrch_*  : se resuelve al agente padre, se evalÃºa la config del
                     agente bajo esa rama. Si include_n8n_flows=True, ademÃ¡s
                     se detectan y evalÃºan los flujos n8n que llama, y se
                     retorna un resultado tipo pipeline.

    Equivalente programÃ¡tico a `python evaluar_elevenlabs.py <ID>`.
    """
    from juez.api.elevenlabs_discovery import detect_id_type

    resolved = (target_id or agent_id or "").strip()
    if not resolved:
        raise ValueError("Debes pasar target_id o agent_id")

    tipo = detect_id_type(resolved)
    if tipo == "branch":
        return _run_elevenlabs_branch(
            branch_id=resolved,
            total_conversaciones=total_conversaciones,
            concurrencia=concurrencia,
            escenarios=escenarios or [],
            openai_key=openai_key,
            elevenlabs_key=elevenlabs_key,
            n8n_api_key=n8n_api_key,
            n8n_base_url=n8n_base_url,
            include_n8n_flows=include_n8n_flows,
            progress_cb=progress_cb,
        )
    if tipo == "version":
        raise ValueError(
            f"Los version IDs ({resolved}) no se evalÃºan directamente. "
            "PÃ¡same el branch_id (agtbrch_*) o el agent_id (agent_*)."
        )
    if tipo == "unknown":
        raise ValueError(
            f"ID '{resolved}' no tiene un prefijo reconocido "
            "(esperado: agent_* o agtbrch_*)."
        )

    # tipo == "agent" â€” flujo original intacto
    progress = progress_cb or _progress_noop
    previo_env = _setear_env_temporal(openai_key=openai_key, elevenlabs_key=elevenlabs_key)
    try:
        progress("Cargando mÃ³dulo ElevenLabs", 5)
        mod = _load_module("evaluar_elevenlabs")

        eleven_key = elevenlabs_key or os.getenv("ELEVENLABS_API_KEY", "")
        if not eleven_key:
            raise ValueError("ELEVENLABS_API_KEY no configurada")

        agent_id_real = resolved
        progress("Descargando agente de ElevenLabs", 15)
        client = mod.ElevenLabsClient(eleven_key)
        data = client.obtener_agente(agent_id_real)
        nombre = data.get("name", agent_id_real)

        progress(f"AnÃ¡lisis estÃ¡tico: {nombre}", 30)
        analisis = mod.ElevenLabsAnalyzer(data).analizar()

        gpt_result: Dict[str, Any] = {}
        oai_key = openai_key or os.getenv("OPENAI_API_KEY", "")
        if oai_key:
            progress("AnÃ¡lisis profundo con GPT", 45)
            try:
                gpt_result = mod.analizar_con_gpt(analisis, nombre)
                analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
            except Exception:
                pass

        # Contra-agente
        batch_result = None
        reporte_ca = ""
        if total_conversaciones > 0 and oai_key:
            try:
                from juez.evaluation.contra_agente.generator import generar_batch as _gen
                from juez.evaluation.contra_agente.evaluator import TurnEvaluator as _TE
                from juez.evaluation.contra_agente.reporter import generar_reporte_batch as _rep
                from juez.evaluation.contra_agente.adapters.elevenlabs import ElevenLabsAdapter
                from juez.evaluation.contra_agente.worker import ConversationWorker
                import concurrent.futures

                progress(f"Generando {total_conversaciones} conversaciones", 55)
                conc = concurrencia or min(max(total_conversaciones // 4, 2), 8)
                batch = _gen(
                    analisis=analisis,
                    agent_name=nombre,
                    total=total_conversaciones,
                    concurrency=conc,
                    adapter="elevenlabs",
                    openai_key=oai_key,
                    escenarios_extra=escenarios or [],
                )

                evaluator = _TE(openai_key=oai_key)

                progress("Ejecutando conversaciones contra el agente", 70)

                def _run_one(plan: Any) -> Any:
                    adapter = ElevenLabsAdapter(
                        agent_id=agent_id_real,
                        analisis=analisis,
                        openai_key=oai_key,
                        el_key=eleven_key,
                    )
                    worker = ConversationWorker(plan, adapter, evaluator, openai_key=oai_key)
                    return worker.run()

                results: List[Any] = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as ex:
                    futures = [ex.submit(_run_one, p) for p in batch.plans]
                    for fut in concurrent.futures.as_completed(futures):
                        try:
                            results.append(fut.result())
                        except Exception:
                            pass

                from juez.evaluation.contra_agente.models import BatchResult
                total = len(results)
                passed = sum(1 for r in results if r.passed)
                by_cat: Dict[str, Dict[str, Any]] = {}
                for r in results:
                    cat = r.category
                    by_cat.setdefault(cat, {"total": 0, "passed": 0, "pass_rate": 0.0})
                    by_cat[cat]["total"] += 1
                    if r.passed:
                        by_cat[cat]["passed"] += 1
                for cat in by_cat:
                    t = by_cat[cat]["total"]
                    by_cat[cat]["pass_rate"] = round(by_cat[cat]["passed"] / t, 3) if t else 0.0

                batch_result = BatchResult(
                    batch_id=batch.batch_id,
                    agent_id=agent_id_real,
                    total=total,
                    passed=passed,
                    failed=total - passed,
                    pass_rate=round(passed / total, 3) if total else 0.0,
                    by_category=by_cat,
                    collapse_pattern={},
                    results=results,
                    recommendations=[],
                    scorecard={},
                )
                reporte_ca = _rep(batch_result, agent_name=nombre)
            except Exception as exc:
                reporte_ca = f"\n[CONTRA-AGENTE â€” Error: {exc}]\n"

        progress("Calculando scorecard", 88)
        scores = mod.calcular_scorecard(analisis, batch_result)

        progress("Generando reporte", 95)
        reporte = mod.generar_reporte(
            analisis, gpt_result, nombre, agent_id_real,
            juez_report=None, scores_precalculados=scores,
        )
        if reporte_ca:
            reporte = reporte + "\n\n" + reporte_ca

        reporte_path = _guardar_reporte_txt(nombre, reporte, "elevenlabs")

        return {
            "kind": "elevenlabs",
            "nombre": nombre,
            "agent_id": agent_id_real,
            "score_general": scores.get("score_general", 0.0),
            "scores": _strip_non_serializable(scores),
            "problemas": _strip_non_serializable(analisis.get("problemas", [])),
            "dynamic_tests_ran": batch_result is not None,
            "batch_summary": (
                {
                    "total": batch_result.total,
                    "passed": batch_result.passed,
                    "pass_rate": batch_result.pass_rate,
                    "by_category": batch_result.by_category,
                }
                if batch_result
                else None
            ),
            "reporte_txt": reporte,
            "reporte_path": reporte_path,
        }
    finally:
        _restaurar_env(previo_env)


def run_n8n_single(
    flow: Dict[str, Any],
    total_conversaciones: int = 20,
    concurrencia: int = 3,
    escenarios: Optional[List[str]] = None,
    openai_key: str = "",
    n8n_api_key: str = "",
    n8n_base_url: str = "",
    evaluate_artifact: bool = True,
    artifact_agent_id: str = "",
    modo_qa: str = "ambos",
    modo_ejecucion: str = "sandbox",
    reference_dataset_id: Optional[str] = None,
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """EvalÃºa un Ãºnico flujo n8n.

    `flow` es un dict tipo `N8nFlowSource`: {url, workflow_id, json_content, webhook_url}.

    `reference_dataset_id`: id de un dataset subido via POST /reference-data/ingest.
    Si contiene un `payload_template` (ejemplo real del sobre que espera el
    webhook, ej. WhatsApp Business API), se usa para disparar las
    conversaciones de prueba con la forma real que el flujo necesita.
    """
    progress = progress_cb or _progress_noop
    payload_template = _resolver_payload_template(reference_dataset_id)
    previo_env = _setear_env_temporal(
        openai_key=openai_key, n8n_api_key=n8n_api_key, n8n_base_url=n8n_base_url
    )
    try:
        progress("Resolviendo origen del flujo n8n", 5)
        wf, webhook_url, nombre = _resolve_n8n_flow(flow, n8n_api_key, n8n_base_url)

        progress(f"Cargando mÃ³dulo n8n", 10)
        mod = _load_module("evaluar_n8n")

        progress(f"AnÃ¡lisis estÃ¡tico: {nombre}", 25)
        analisis = mod.N8nAnalyzer(wf).analizar()

        gpt_result: Dict[str, Any] = {}
        oai_key = openai_key or os.getenv("OPENAI_API_KEY", "")
        if oai_key:
            progress("AnÃ¡lisis profundo con GPT", 40)
            try:
                gpt_result = mod.analizar_con_gpt(analisis, nombre)
                analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
            except Exception:
                pass

            progress("ValidaciÃ³n dinÃ¡mica de modelos LLM", 50)
            try:
                analisis = mod.validar_y_enriquecer_modelos(analisis, oai_key)
            except Exception:
                pass

        # Contra-agente
        batch_result = None
        reporte_ca = ""
        webhook_activo_msg = ""
        # Sin payload_template explicito, inferir el sobre esperado desde el wf
        # para que el flujo reciba el texto en su ruta real (cualquier estructura).
        envelope_hint = None
        if not payload_template:
            try:
                envelope_hint = mod.inferir_envelope_desde_wf(wf)
            except Exception:
                envelope_hint = None
        if total_conversaciones > 0 and webhook_url and oai_key:
            if modo_ejecucion == "sandbox":
                progress(f"Simulando {total_conversaciones} conversaciones", 65)
                try:
                    batch_result, reporte_ca = mod.ejecutar_contra_agente(
                        analisis_n8n=analisis,
                        webhook_url=webhook_url,
                        agent_name=nombre,
                        total_conv=total_conversaciones,
                        concurrencia=concurrencia,
                        escenarios_extra=escenarios or [],
                        modo_ejecucion="sandbox",
                        payload_template=payload_template,
                        envelope_hint=envelope_hint,
                    )
                    webhook_activo_msg = "sandbox: webhook no invocado"
                except Exception as exc:
                    reporte_ca = f"\n[CONTRA-AGENTE - Error: {exc}]\n"
            else:
                pipeline_mod = _load_module("evaluar_pipeline")
                activo, msg = pipeline_mod._verificar_webhook_activo(webhook_url, payload_template=payload_template)
                webhook_activo_msg = msg

                if activo:
                    progress(f"Ejecutando {total_conversaciones} conversaciones", 65)
                    try:
                        batch_result, reporte_ca = mod.ejecutar_contra_agente(
                            analisis_n8n=analisis,
                            webhook_url=webhook_url,
                            agent_name=nombre,
                            total_conv=total_conversaciones,
                            concurrencia=concurrencia,
                            escenarios_extra=escenarios or [],
                            modo_ejecucion="real",
                            payload_template=payload_template,
                            envelope_hint=envelope_hint,
                        )
                    except Exception as exc:
                        reporte_ca = f"\n[CONTRA-AGENTE - Error: {exc}]\n"
                else:
                    reporte_ca = (
                        "\n[CONTRA-AGENTE] Webhook no activo: " + msg +
                        "\nActiva el flujo en n8n para correr las pruebas dinamicas.\n"
                    )

        # QA de artefacto (aditivo, opcional)
        artef: Dict[str, Any] = {}
        if evaluate_artifact:
            progress("QA de artefacto (disparo sintetico + evaluacion de salida)", 80)
            try:
                from juez.evaluation.artifact import run_artifact_eval
                aid = artifact_agent_id or nombre.lower().replace(" ", "_")[:40]
                artef = run_artifact_eval(aid)
                if artef:
                    analisis.setdefault("problemas", []).extend(artef.get("problemas", []))
            except Exception as exc:
                artef = {"error": str(exc)}

        # Copia SIN filtrar -- el informe no tecnico siempre muestra las 3
        # secciones completas (seguridad/funcional/tecnico), independiente
        # de modo_qa (que solo afecta el score y el reporte tecnico clasico).
        todos_los_problemas = list(analisis.get("problemas", []))

        # Modo QA: filtra los hallazgos a técnico / funcional antes de puntuar.
        if modo_qa and modo_qa != "ambos":
            from juez.evaluation.qa_mode import filtrar_problemas
            analisis["problemas"] = filtrar_problemas(analisis.get("problemas", []), modo_qa)

        progress("Calculando scores", 88)
        scores = mod.calcular_score_n8n(analisis, batch_result)
        if artef and not artef.get("error"):
            scores.setdefault("por_categoria", {})["artefacto"] = artef.get("score_artefacto", 0.0)

        # Informe no tecnico: es una presentacion ADICIONAL sobre datos ya
        # calculados arriba -- si falla por lo que sea, NUNCA debe tumbar la
        # evaluacion completa (que ya tiene su score y sus problemas listos).
        informe_no_tecnico = ""
        try:
            score_general = scores.get("score_general", 0.0)
            if score_general >= 70:
                veredicto_no_tecnico = "cumple"
            elif score_general >= 50:
                veredicto_no_tecnico = "cumple_parcial"
            else:
                veredicto_no_tecnico = "no_cumple"

            from juez.evaluation.reporting.legible import render_informe_no_tecnico
            informe_no_tecnico = render_informe_no_tecnico(
                titulo=f"Evaluación del flujo: {nombre}",
                veredicto=veredicto_no_tecnico,
                score=score_general,
                problemas=todos_los_problemas,
                que_se_evaluo="Seguridad, funcionamiento y construcción técnica del flujo n8n.",
            )
        except Exception:
            informe_no_tecnico = ""

        progress("Generando reporte", 95)
        archivo_origen = flow.get("url") or flow.get("workflow_id") or "(JSON inline via API)"
        reporte = mod.generar_reporte(analisis, gpt_result, nombre, archivo_origen)
        if reporte_ca:
            reporte = reporte + "\n\n" + reporte_ca
        if artef and artef.get("reporte"):
            reporte = reporte + "\n\n" + artef["reporte"]
        informe_enfoques = _build_informe_enfoques(
            kind="n8n",
            nombre=nombre,
            score_general=scores.get("score_general", 0.0),
            problemas=analisis.get("problemas", []),
            batch_result=batch_result,
            trigger=analisis.get("trigger", {}),
            webhook_status=webhook_activo_msg or ("activo" if batch_result else "no probado"),
        )
        reporte = reporte + "\n\n" + _render_informe_enfoques_txt(informe_enfoques)

        reporte_path = _guardar_reporte_txt(nombre, reporte, "n8n")

        return {
            "kind": "n8n",
            "nombre": nombre,
            "webhook_url": webhook_url,
            "webhook_status": webhook_activo_msg or ("activo" if batch_result else "no probado"),
            "score_general": scores.get("score_general", 0.0),
            "scores": _strip_non_serializable(scores),
            "problemas": _strip_non_serializable(analisis.get("problemas", [])),
            "informe_no_tecnico": informe_no_tecnico,
            "trigger": _strip_non_serializable(analisis.get("trigger", {})),
            "dynamic_tests_ran": batch_result is not None,
            "batch_summary": (
                {
                    "total": batch_result.total,
                    "passed": batch_result.passed,
                    "pass_rate": batch_result.pass_rate,
                    "by_category": batch_result.by_category,
                }
                if batch_result
                else None
            ),
            "reporte_txt": reporte,
            "reporte_path": reporte_path,
            "informe_enfoques": _strip_non_serializable(informe_enfoques),
            "artifact": _strip_non_serializable(artef) if artef else None,
        }
    finally:
        _restaurar_env(previo_env)


def run_pipeline(
    nombre: str = "Pipeline",
    eleven_ids: Optional[List[str]] = None,
    n8n_flows: Optional[List[Dict[str, Any]]] = None,
    total_conversaciones: int = 20,
    concurrencia: int = 3,
    escenarios: Optional[List[str]] = None,
    modo_ejecucion: str = "real",
    reference_dataset_id: Optional[str] = None,
    openai_key: str = "",
    elevenlabs_key: str = "",
    n8n_api_key: str = "",
    n8n_base_url: str = "",
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """EvalÃºa un pipeline completo: 0..N agentes ElevenLabs + 0..M flujos n8n.

    Replica la lÃ³gica de `evaluar_pipeline.py main()` sin el menÃº interactivo.

    `reference_dataset_id`: ver run_n8n_single -- si el dataset trae
    payload_template, se usa para disparar cada flujo n8n del pipeline con
    la forma real que espera (ej. WhatsApp Business API).
    """
    payload_template = _resolver_payload_template(reference_dataset_id)
    progress = progress_cb or _progress_noop
    eleven_ids = eleven_ids or []
    n8n_flows = n8n_flows or []

    if not eleven_ids and not n8n_flows:
        raise ValueError("El pipeline necesita al menos un agente ElevenLabs o un flujo n8n")

    previo_env = _setear_env_temporal(
        openai_key=openai_key,
        elevenlabs_key=elevenlabs_key,
        n8n_api_key=n8n_api_key,
        n8n_base_url=n8n_base_url,
    )
    try:
        oai_key = openai_key or os.getenv("OPENAI_API_KEY", "")
        eleven_key = elevenlabs_key or os.getenv("ELEVENLABS_API_KEY", "")

        progress("Cargando mÃ³dulos", 3)
        pipeline_mod = _load_module("evaluar_pipeline")
        n8n_mod = _load_module("evaluar_n8n")
        eleven_mod = _load_module("evaluar_elevenlabs")

        total_nodos = len(eleven_ids) + len(n8n_flows)
        previews: List[Dict[str, Any]] = []
        flujos_fallidos: List[Dict[str, str]] = []

        # â”€â”€ Fase 1: ElevenLabs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for i, agent_id in enumerate(eleven_ids):
            pct = 5 + int((i / max(total_nodos, 1)) * 30)
            progress(f"Analizando ElevenLabs ({i + 1}/{len(eleven_ids)}): {agent_id}", pct)
            try:
                data = eleven_mod.ElevenLabsClient(eleven_key).obtener_agente(agent_id)
                name = data.get("name", agent_id)
                analisis = eleven_mod.ElevenLabsAnalyzer(data).analizar()
                gpt_result: Dict[str, Any] = {}
                if oai_key:
                    try:
                        gpt_result = eleven_mod.analizar_con_gpt(analisis, name)
                        analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
                    except Exception:
                        pass
                previews.append({
                    "nombre": name, "tipo": "elevenlabs", "analisis": analisis,
                    "gpt_result": gpt_result, "agent_id": agent_id,
                    "webhook": "", "wf_data": None, "json_path": "", "ajustado": False,
                })
            except Exception as exc:
                flujos_fallidos.append({"url": agent_id, "etapa": "analisis", "error": str(exc)})

        # â”€â”€ Fase 1: n8n â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for i, flow in enumerate(n8n_flows):
            pct = 35 + int((i / max(len(n8n_flows), 1)) * 25)
            origen = flow.get("url") or flow.get("workflow_id") or "flujo-n8n"
            progress(f"Analizando n8n ({i + 1}/{len(n8n_flows)}): {origen}", pct)
            try:
                wf, webhook_url, name = _resolve_n8n_flow(flow, n8n_api_key, n8n_base_url)
                analisis = n8n_mod.N8nAnalyzer(wf).analizar()
                gpt_result = {}
                if oai_key:
                    try:
                        gpt_result = n8n_mod.analizar_con_gpt(analisis, name)
                        analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
                    except Exception:
                        pass
                    try:
                        analisis = n8n_mod.validar_y_enriquecer_modelos(analisis, oai_key)
                    except Exception:
                        pass
                previews.append({
                    "nombre": name, "tipo": "n8n", "analisis": analisis,
                    "gpt_result": gpt_result, "agent_id": "",
                    "webhook": webhook_url, "wf_data": wf,
                    "json_path": "", "ajustado": False,
                })
            except Exception as exc:
                flujos_fallidos.append({"url": origen, "etapa": "analisis", "error": str(exc)})

        if not previews:
            raise RuntimeError(
                "No se pudo analizar ningÃºn nodo. Errores: "
                + "; ".join(f"{f['url']}: {f['error']}" for f in flujos_fallidos)
            )

        # â”€â”€ Fase 2: pruebas dinÃ¡micas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        node_results: List[Any] = []
        for i, pv in enumerate(previews):
            pct = 60 + int((i / max(len(previews), 1)) * 25)
            progress(f"Probando {pv['nombre']} ({i + 1}/{len(previews)})", pct)
            try:
                result = pipeline_mod._evaluar_con_analisis(
                    pv, oai_key, total_conversaciones, concurrencia,
                    modo_ejecucion=modo_ejecucion, escenarios_extra=escenarios,
                    payload_template=payload_template if pv["tipo"] == "n8n" else None,
                )
                node_results.append(result)
            except Exception as exc:
                flujos_fallidos.append({"url": pv["nombre"], "etapa": "pruebas", "error": str(exc)})

        if not node_results:
            detalles = "; ".join(
                f"{f['url']} [{f['etapa']}]: {f['error']}" for f in flujos_fallidos
            ) or "(sin detalles)"
            raise RuntimeError(
                f"No se pudo evaluar ningÃºn nodo en la fase dinÃ¡mica. Errores: {detalles}"
            )

        # â”€â”€ Grafo del pipeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        progress("Construyendo grafo del pipeline", 88)
        graph = None
        try:
            from juez.evaluation.pipeline.graph import build_pipeline_graph
            nodes_data = [
                {
                    "node_id":     r.node_id,
                    "node_type":   r.node_type,
                    "name":        r.name,
                    "analisis":    r.analisis,
                    "raw_flow":    r.raw_flow,
                    "scores":      r.scores,
                    "batch_result": r.batch_result,
                }
                for r in node_results
            ]
            graph = build_pipeline_graph(nodes_data)
        except Exception:
            graph = None

        # â”€â”€ AnÃ¡lisis de coherencia â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        progress("Analizando coherencia del pipeline", 92)
        analisis_coherencia: Dict[str, Any] = {}
        try:
            from juez.evaluation.pipeline.analyzer import analizar_coherencia_pipeline
            nodes_summary = [
                {"name": r.name, "node_type": r.node_type, "analisis": r.analisis, "scores": r.scores}
                for r in node_results
            ]
            edges_summary = [
                {"source": e.source_id, "target": e.target_id, "match_type": e.match_type}
                for e in (graph.edges if graph else [])
            ]
            order = graph.order if graph else [r.node_id for r in node_results]
            gaps_data: List[Dict[str, str]] = []
            for _g in (graph.gaps if graph else []):
                _src_node = graph.nodes.get(_g.node_id) if graph else None
                _caller_name = _src_node.name if _src_node else _g.node_id
                gaps_data.append({
                    "node_id":     _g.node_id,
                    "exit_url":    _g.exit_url,
                    "description": _g.description,
                    "caller":      _caller_name,
                    "endpoint":    _g.exit_url,
                })
            analisis_coherencia = analizar_coherencia_pipeline(
                nodes_data=nodes_summary,
                edges=edges_summary,
                order=order,
                gaps=gaps_data,
                openai_key=oai_key,
            )
        except Exception as exc:
            analisis_coherencia = {
                "score_coherencia": 70.0, "riesgos": [],
                "recomendaciones": [], "_error": str(exc),
            }

        # â”€â”€ Score final del pipeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        scores_pipeline = pipeline_mod.calcular_score_pipeline(node_results, analisis_coherencia)
        pipeline_name = nombre or f"Pipeline ({', '.join(r.name for r in node_results[:2])})"

        # â”€â”€ Reporte â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        progress("Generando reporte del pipeline", 96)
        reporte_pipeline = ""
        try:
            from juez.evaluation.pipeline.reporter import generar_reporte_pipeline
            agent_results_for_report = [
                {"name": r.name, "node_type": r.node_type, "scores": r.scores, "reporte_texto": r.reporte_texto}
                for r in node_results
            ]
            analisis_coherencia_completo = {**analisis_coherencia, **scores_pipeline}
            reporte_pipeline = generar_reporte_pipeline(
                graph=graph,
                analisis_pipeline=analisis_coherencia_completo,
                agent_results=agent_results_for_report,
                pipeline_name=pipeline_name,
            )
        except Exception as exc:
            reporte_pipeline = f"\n[REPORTE DEL PIPELINE â€” Error: {exc}]\n"

        # SecciÃ³n de flujos fallidos (si aplica)
        if flujos_fallidos:
            SEP80 = "=" * 80
            lineas = [
                "", SEP80, "  FLUJOS NO ANALIZADOS", SEP80, "",
                f"  {len(flujos_fallidos)} flujo(s) no pudieron ser procesados:", "",
            ]
            for i, f in enumerate(flujos_fallidos, 1):
                lineas.extend([
                    f"  [{i}] {f['url']}",
                    f"      Etapa : {f['etapa']}",
                    f"      Error : {f['error'][:200]}",
                    "",
                ])
            reporte_pipeline = reporte_pipeline + "\n" + "\n".join(lineas)


        # â”€â”€ Resultado serializable â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        nodos_out: List[Dict[str, Any]] = []
        for r in node_results:
            node_informe = _build_informe_enfoques(
                kind=r.node_type,
                nombre=r.name,
                score_general=r.scores.get("score_general", 0.0),
                problemas=r.analisis.get("problemas", []),
                batch_result=r.batch_result,
                trigger=r.analisis.get("trigger", {}) if r.node_type == "n8n" else {},
                webhook_status="probado" if r.dynamic_tests_ran else "no probado",
            )
            nodos_out.append({
                "node_id": r.node_id,
                "node_type": r.node_type,
                "name": r.name,
                "score_general": r.scores.get("score_general", 0.0),
                "scores": _strip_non_serializable(r.scores),
                "problemas": _strip_non_serializable(r.analisis.get("problemas", [])),
                "trigger": _strip_non_serializable(r.analisis.get("trigger", {})) if r.node_type == "n8n" else None,
                "dynamic_tests_ran": r.dynamic_tests_ran,
                "batch_summary": _batch_focus_summary(r.batch_result),
                "informe_enfoques": _strip_non_serializable(node_informe),
            })

        edges_out: List[Dict[str, Any]] = []
        if graph:
            for e in graph.edges:
                edges_out.append({
                    "source": e.source_id,
                    "target": e.target_id,
                    "match_type": e.match_type,
                })

        gaps_out: List[Dict[str, Any]] = []
        if graph:
            for g in graph.gaps:
                node = graph.nodes.get(g.node_id)
                gaps_out.append({
                    "node_id": g.node_id,
                    "caller": node.name if node else g.node_id,
                    "exit_url": g.exit_url,
                    "description": g.description,
                })

        problemas_pipeline: List[Dict[str, Any]] = []
        for r in node_results:
            problemas_pipeline.extend(r.analisis.get("problemas", []) or [])
        for f in flujos_fallidos:
            problemas_pipeline.append({
                "severidad": "ALTO",
                "tipo": "Flujo no analizado",
                "descripcion": f"{f.get('url')} fallo en etapa {f.get('etapa')}: {f.get('error')}",
                "nodo": f.get("url", ""),
            })
        informe_enfoques = _build_informe_enfoques(
            kind="pipeline",
            nombre=pipeline_name,
            score_general=scores_pipeline.get("score_general", 0.0),
            problemas=problemas_pipeline,
            nodos=nodos_out,
            flujos_fallidos=flujos_fallidos,
            pruebas_summary=_aggregate_pruebas_summary(nodos_out),
        )
        reporte_pipeline = reporte_pipeline + "\n\n" + _render_informe_enfoques_txt(informe_enfoques)
        reporte_path = _guardar_reporte_txt(pipeline_name, reporte_pipeline, "pipeline")

        return {
            "kind": "pipeline",
            "nombre": pipeline_name,
            "score_general": scores_pipeline.get("score_general", 0.0),
            "scores": _strip_non_serializable(scores_pipeline),
            "coherencia": _strip_non_serializable(analisis_coherencia),
            "nodos": nodos_out,
            "edges": edges_out,
            "gaps": gaps_out,
            "ciclos": list(graph.cycles) if graph else [],
            "orden_topologico": list(graph.order) if graph else [],
            "nodos_solo_estatico": scores_pipeline.get("nodos_solo_estatico", []),
            "flujos_fallidos": flujos_fallidos,
            "reporte_txt": reporte_pipeline,
            "reporte_path": reporte_path,
            "informe_enfoques": _strip_non_serializable(informe_enfoques),
        }
    finally:
        _restaurar_env(previo_env)


def _escenarios_desde_registros(reference_dataset_id: Optional[str], maximo: int = 3) -> List[str]:
    """Convierte hasta `maximo` records reales de un dataset de referencia en
    escenarios descriptivos (texto libre) para que el generador de
    conversaciones use datos REALES en vez de inventados. No lanza."""
    if not reference_dataset_id:
        return []
    try:
        from juez.api.reference_store import get_reference_store
        dataset = get_reference_store().get(reference_dataset_id)
    except Exception:
        return []
    if not dataset or not dataset.records:
        return []
    escenarios: List[str] = []
    for rec in dataset.records[:maximo]:
        partes = ", ".join(f"{k}: {v}" for k, v in rec.items() if v not in (None, ""))
        if partes:
            escenarios.append(f"Usa este caso real como base para una conversacion: {partes}")
    return escenarios


def _resolver_payload_template(reference_dataset_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resuelve un reference_dataset_id a su payload_template (si tiene uno).

    Nunca lanza: un id invalido o un dataset sin payload_template simplemente
    deja las pruebas en el comportamiento generico de siempre."""
    if not reference_dataset_id:
        return None
    try:
        from juez.api.reference_store import get_reference_store
        dataset = get_reference_store().get(reference_dataset_id)
        return dataset.payload_template if dataset else None
    except Exception:
        return None


def _slug_archivo(nombre: str, fallback: str = "flujo") -> str:
    limpio = re.sub(r"[^\w\-]", "_", (nombre or "").strip())[:60]
    return limpio or fallback


def _construir_proyecto_temporal(
    prompt: str,
    componentes_n8n: List[tuple],
    reglas_negocio: Optional[List[str]],
    objetivos_por_flujo: Optional[Dict[str, List[Dict[str, Any]]]],
) -> Path:
    """Materializa prompt + flujos n8n + reglas/objetivos en una carpeta
    temporal, para reusar evaluate_project_path (capa MODERNA de Colmena:
    workers.py, business_rules.py, objectives.py, purpose_check) en vez de
    la capa legacy (run_colmena directo), que no conoce nada de esto.
    """
    root = Path(tempfile.mkdtemp(prefix="juez_proyecto_"))
    if prompt.strip():
        (root / "agente_prompt.txt").write_text(
            f"System prompt del agente (instrucciones):\n{prompt}", encoding="utf-8"
        )
    for wf_nombre, wf in componentes_n8n:
        archivo = f"{_slug_archivo(wf_nombre)}.json"
        (root / archivo).write_text(json.dumps(wf, ensure_ascii=False), encoding="utf-8")
    if reglas_negocio:
        reglas_json = {
            "reglas": [
                {"id": f"RN-API-{i + 1:03d}", "descripcion": r}
                for i, r in enumerate(reglas_negocio) if r and r.strip()
            ]
        }
        (root / "reglas_negocio.json").write_text(json.dumps(reglas_json, ensure_ascii=False), encoding="utf-8")
    if objetivos_por_flujo:
        (root / "objetivos_flujos.json").write_text(json.dumps(objetivos_por_flujo, ensure_ascii=False), encoding="utf-8")
    return root


class _ColmenaLegacyView:
    """Adapta un ProjectEvaluationReport (capa moderna) al shape que
    `consolidar_proyecto()` ya sabe leer (.hallazgos como lista de dicts,
    .score como float, .model_dump() para el 'detalle' de trazabilidad) --
    para reusar esa consolidacion sin duplicarla ni tocar mejoras.py."""

    def __init__(self, report: Any) -> None:
        self.hallazgos = [
            {
                "obrera": f.source or f.category,
                "severidad": f.severity,
                "descripcion": f"[{report.project_id}] {f.title}: {f.description}",
                "ubicacion": f.file or "",
                "accion": f.recommendation or "",
            }
            for f in report.findings
        ]
        self.score = report.score.score
        self._report = report

    def model_dump(self, mode: str = "python") -> Dict[str, Any]:
        try:
            return self._report.model_dump(mode=mode)
        except AttributeError:
            return {"hallazgos": self.hallazgos, "score": self.score}


def _project_report_a_colmena_legacy(report) -> Any:
    return _ColmenaLegacyView(report)


def _ejecucion_transparencia(
    *,
    modo_ejecucion: str,
    incluir_conversaciones: bool,
    incluir_dinamicas: bool,
    hay_agentes: bool,
    conversacion: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Declara, por CAPA, si el resultado salio de una ejecucion REAL contra el
    agente o de una SIMULACION. Sin esto, un resultado simulado (sandbox / juez
    que imagina la respuesta) se ve igual que uno real -- y confundir ambos es
    justo lo que destruye la credibilidad de un juez. Es transparencia pura:
    cada capa dice de donde viene su veredicto.
    """
    conv_ejecutada = bool(conversacion) and not (isinstance(conversacion, dict) and conversacion.get("error"))
    conv_fallida = isinstance(conversacion, dict) and bool(conversacion.get("error"))

    if not incluir_conversaciones or not hay_agentes:
        conversaciones = "no_ejecutadas"
        conv_desc = "No se corrieron conversaciones (solo analisis estatico de construccion)."
    elif conv_fallida:
        conversaciones = "fallida"
        conv_desc = "Se intento ejecutar pero el agente/flujo fallo (ver flujos_fallidos)."
    elif modo_ejecucion == "real":
        conversaciones = "real"
        conv_desc = "Ejecucion REAL: se disparo el webhook n8n / agente ElevenLabs de verdad."
    else:
        conversaciones = "simulado"
        conv_desc = "Sandbox: el agente se SIMULA con un LLM; no se dispara el webhook real."

    return {
        "modo": modo_ejecucion if incluir_conversaciones else "sin_conversaciones",
        "capas": {
            "analisis_estatico": {
                "tipo": "real",
                "detalle": "Analisis determinista del prompt/flujo/codigo tal como esta escrito.",
            },
            "conversaciones": {"tipo": conversaciones, "detalle": conv_desc},
            "obreras_dinamicas": {
                "tipo": "simulado" if incluir_dinamicas else "no_ejecutadas",
                "detalle": (
                    "El LLM SIMULA la respuesta del agente y la juzga; no ejecuta el agente real."
                    if incluir_dinamicas else "Obreras dinamicas desactivadas (incluir_dinamicas=False)."
                ),
            },
        },
        "nota": (
            "Cada capa declara si su veredicto viene de ejecucion REAL o de SIMULACION. "
            "'real' = se golpeo el agente/flujo de verdad; 'simulado' = un LLM imito su comportamiento."
        ),
    }


def run_proyecto(
    nombre: str = "Proyecto",
    prompt: str = "",
    eleven_ids: Optional[List[str]] = None,
    n8n_flows: Optional[List[Dict[str, Any]]] = None,
    total_conversaciones: int = 10,
    concurrencia: int = 3,
    escenarios: Optional[List[str]] = None,
    incluir_conversaciones: bool = True,
    incluir_dinamicas: bool = False,
    modo_ejecucion: str = "sandbox",
    reglas_negocio: Optional[List[str]] = None,
    objetivos: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    reference_dataset_id: Optional[str] = None,
    openai_key: str = "",
    elevenlabs_key: str = "",
    n8n_api_key: str = "",
    n8n_base_url: str = "",
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """EvaluaciÃ³n UNIFICADA de un proyecto â†’ contrato firme.

    Corre dos motores y los consolida en un solo JSON estable (ver
    `juez.colmena.mejoras.consolidar_proyecto`):

      1) La Colmena MODERNA (`evaluate_project_path`): analisis de
         construccion (seguridad, flujos, arquitectura, calidad del prompt,
         REGLAS DE NEGOCIO explicitas via `reglas_negocio`, OBJETIVOS
         declarados via `objetivos`, + obreras dinamicas opt-in: adversarial/
         edge/proposito). Barato y determinista en su parte estatica.
      2) Conversaciones (`run_pipeline`): simulacion contra el/los agente(s).
         Opcional (caro / tarda) â€” se corre solo si `incluir_conversaciones`.

    Devuelve `score`, `estado`, `problemas[]` y `mejoras[]`, donde la mejora del
    prompt trae `antes`/`despues` REALES listos para aplicar.
    """
    eleven_ids = eleven_ids or []
    n8n_flows = n8n_flows or []
    prompt = (prompt or "").strip()

    if not prompt and not eleven_ids and not n8n_flows:
        raise ValueError(
            "El proyecto necesita al menos un prompt, un agente ElevenLabs o un flujo n8n."
        )

    progress = progress_cb or _progress_noop
    previo_env = _setear_env_temporal(
        openai_key=openai_key,
        elevenlabs_key=elevenlabs_key,
        n8n_api_key=n8n_api_key,
        n8n_base_url=n8n_base_url,
    )
    try:
        from juez.colmena.mejoras import consolidar_proyecto
        from juez.colmena.project_evaluator import evaluate_project_path

        # â”€â”€ 1) Componentes para La Colmena â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        progress("Preparando componentes del proyecto", 5)
        componentes_n8n: List[tuple] = []
        for flow in n8n_flows:
            try:
                wf, _webhook, wf_nombre = _resolve_n8n_flow(flow, n8n_api_key, n8n_base_url)
                componentes_n8n.append((wf_nombre, wf))
            except Exception:
                # Un flujo no resoluble no bloquea la evaluaciÃ³n; La Colmena
                # simplemente no lo analiza. La conversaciÃ³n reportarÃ¡ su fallo.
                pass

        # â”€â”€ 2) La Colmena (construcciÃ³n, capa MODERNA) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        colmena = None
        cobertura: Dict[str, Any] = {}
        if prompt or componentes_n8n:
            progress("Analizando la construcciÃ³n del proyecto", 25)
            root_tmp = _construir_proyecto_temporal(prompt, componentes_n8n, reglas_negocio, objetivos)
            try:
                project_report = evaluate_project_path(
                    root_tmp, project_id=nombre or "proyecto", incluir_dinamicas=incluir_dinamicas,
                )
                colmena = _project_report_a_colmena_legacy(project_report)
                cobertura = dict(getattr(project_report, "coverage", {}) or {})
            finally:
                shutil.rmtree(root_tmp, ignore_errors=True)

        # â”€â”€ 3) Conversaciones (opcional, caro) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        conversacion: Optional[Dict[str, Any]] = None
        if incluir_conversaciones and (eleven_ids or n8n_flows):
            progress("Probando el agente con conversaciones", 45)
            escenarios_enriquecidos = list(escenarios or []) + _escenarios_desde_registros(reference_dataset_id)
            try:
                conversacion = run_pipeline(
                    nombre=nombre,
                    eleven_ids=eleven_ids,
                    n8n_flows=n8n_flows,
                    total_conversaciones=total_conversaciones,
                    concurrencia=concurrencia,
                    escenarios=escenarios_enriquecidos,
                    modo_ejecucion=modo_ejecucion,
                    reference_dataset_id=reference_dataset_id,
                    openai_key=openai_key,
                    elevenlabs_key=elevenlabs_key,
                    n8n_api_key=n8n_api_key,
                    n8n_base_url=n8n_base_url,
                    progress_cb=None,
                )
            except Exception as exc:
                conversacion = {"error": str(exc)}

        # â”€â”€ 4) Consolidar contrato firme (+ antes/despuÃ©s del prompt) â”€â”€â”€â”€â”€â”€â”€â”€â”€
        progress("Consolidando resultado y proponiendo mejoras", 90)
        contrato = consolidar_proyecto(
            nombre=nombre or "Proyecto",
            prompt_actual=prompt,
            colmena=colmena,
            conversacion=conversacion,
            openai_key=openai_key,
        )
        contrato["modo_ejecucion"] = modo_ejecucion if incluir_conversaciones else "sin_conversaciones"
        contrato["ejecucion"] = _ejecucion_transparencia(
            modo_ejecucion=modo_ejecucion,
            incluir_conversaciones=incluir_conversaciones,
            incluir_dinamicas=incluir_dinamicas,
            hay_agentes=bool(eleven_ids or n8n_flows),
            conversacion=conversacion,
        )
        nodos_conversacion = []
        flujos_fallidos = []
        if isinstance(conversacion, dict):
            nodos_conversacion = conversacion.get("nodos") or []
            if conversacion.get("error"):
                flujos_fallidos = [{
                    "name": nombre or "Proyecto",
                    "error": str(conversacion.get("error") or ""),
                }]
        informe_enfoques = _build_informe_enfoques(
            kind="proyecto",
            nombre=nombre or "Proyecto",
            score_general=contrato.get("score") or 0.0,
            problemas=contrato.get("problemas", []),
            nodos=nodos_conversacion,
            flujos_fallidos=flujos_fallidos,
            pruebas_summary=_aggregate_pruebas_summary(nodos_conversacion),
        )
        contrato["informe_enfoques"] = _strip_non_serializable(informe_enfoques)
        contrato["reporte_txt"] = _render_informe_enfoques_txt(informe_enfoques)
        # Indice de cobertura: que se evaluo y que quedo fuera (y como activarlo).
        # Reconciliamos la dimension de conversaciones con lo que REALMENTE paso
        # en run_proyecto (eje incluir_conversaciones, distinto del flag estatico).
        if cobertura.get("dimensiones"):
            corrio_conv = bool(conversacion) and not (isinstance(conversacion, dict) and conversacion.get("error"))
            if corrio_conv:
                cobertura["dimensiones"]["conversaciones_dinamicas"] = {"estado": "evaluada"}
                resumen = cobertura.get("resumen", {})
                resumen["omitidas_detalle"] = [
                    d for d in resumen.get("omitidas_detalle", [])
                    if d.get("dimension") != "conversaciones_dinamicas"
                ]
                resumen["omitidas"] = max(0, resumen.get("omitidas", 0) - 1)
                resumen["evaluadas"] = resumen.get("evaluadas", 0) + 1
                cobertura["completa"] = not resumen.get("omitidas") and not resumen.get("parciales")
        contrato["cobertura"] = cobertura
        progress("Listo", 100)
        return contrato
    finally:
        _restaurar_env(previo_env)


# =============================================================================
# SELF-HEAL — propone Y VERIFICA fixes reales sobre el proyecto (antes/despues)
# =============================================================================


_ARCHIVOS_NO_CANDIDATOS_SELF_HEAL = {"reglas_negocio.json", "objetivos_flujos.json"}


def run_proyecto_self_heal(
    nombre: str = "Proyecto",
    prompt: str = "",
    n8n_flows: Optional[List[Dict[str, Any]]] = None,
    reglas_negocio: Optional[List[str]] = None,
    objetivos: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    min_confidence: float = 0.85,
    max_iterations: int = 3,
    max_lines_per_fix: int = 40,
    enable_generic_fixer: bool = False,
    openai_key: str = "",
    n8n_api_key: str = "",
    n8n_base_url: str = "",
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """Corre el self-heal autonomo de La Colmena (`juez.colmena.self_heal_agent.
    run_self_heal`) sobre el MISMO proyecto temporal efimero que arma
    `run_proyecto` (prompt + flujos n8n materializados en una carpeta temporal
    descartable) -- nunca sobre un repo real. Por eso el gate humano de
    self-heal (que es autonomo, sin pausa para aprobacion) es seguro aqui: lo
    unico que puede tocar es ese directorio temporal, que se borra al salir.

    Devuelve antes/despues REAL por archivo (prompt y/o flujos n8n) que
    self-heal dejo aplicado (`kept`), listo para que Gamma lo muestre igual
    que `mejoras[]` de `consolidar_proyecto` -- pero producido por un motor
    que itera, re-evalua y revierte solo, no solo reescribe el prompt.
    """
    from juez.colmena.self_heal_agent import run_self_heal

    n8n_flows = n8n_flows or []
    prompt = (prompt or "").strip()
    if not prompt and not n8n_flows:
        raise ValueError("El self-heal necesita al menos un 'prompt' o un flujo n8n.")

    progress = progress_cb or _progress_noop
    previo_env = _setear_env_temporal(openai_key=openai_key, n8n_api_key=n8n_api_key, n8n_base_url=n8n_base_url)
    try:
        progress("Preparando componentes del proyecto", 5)
        componentes_n8n: List[tuple] = []
        for flow in n8n_flows:
            try:
                wf, _webhook, wf_nombre = _resolve_n8n_flow(flow, n8n_api_key, n8n_base_url)
                componentes_n8n.append((wf_nombre, wf))
            except Exception:
                pass

        root_tmp = _construir_proyecto_temporal(prompt, componentes_n8n, reglas_negocio, objetivos)
        try:
            antes: Dict[str, str] = {
                p.name: p.read_text(encoding="utf-8")
                for p in root_tmp.iterdir()
                if p.is_file() and p.name not in _ARCHIVOS_NO_CANDIDATOS_SELF_HEAL
            }

            progress("Corriendo self-heal (proponer y verificar)", 30)
            resultado = run_self_heal(
                root_tmp,
                min_confidence=min_confidence,
                max_iterations=max_iterations,
                max_lines_per_fix=max_lines_per_fix,
                output_dir=root_tmp / "_selfheal_output",
                enable_generic_fixer=enable_generic_fixer,
            )

            progress("Comparando antes/despues", 90)
            propuestas: List[Dict[str, Any]] = []
            for archivo, texto_antes in antes.items():
                ruta = root_tmp / archivo
                texto_despues = ruta.read_text(encoding="utf-8") if ruta.is_file() else texto_antes
                if texto_despues != texto_antes:
                    propuestas.append({
                        "archivo": archivo,
                        "antes": texto_antes,
                        "despues": texto_despues,
                        "aplicable": True,
                    })

            import dataclasses

            progress("Listo", 100)
            return {
                "kind": "self_heal",
                "nombre": nombre or "Proyecto",
                "score_inicial": resultado.score_initial,
                "score_final": resultado.score_final,
                "readiness_inicial": resultado.readiness_initial,
                "readiness_final": resultado.readiness_final,
                "propuestas": propuestas,
                "resumen": {
                    "aplicados": resultado.kept_fixes,
                    "revertidos": resultado.rolled_back_fixes,
                    "bloqueados": resultado.blocked_findings,
                    "fallidos": resultado.failed_fixes,
                },
                "requiere_revision_manual": resultado.human_review_required,
                "iteraciones": [dataclasses.asdict(it) for it in resultado.iterations],
                "nota": (
                    "Corrido sobre un proyecto temporal efimero (no un repo real): "
                    "las propuestas antes/despues son seguras de mostrar, pero aplicarlas "
                    "de verdad al agente (prompt real / flujo n8n real) es una accion "
                    "aparte que decide un humano en Gamma."
                ),
            }
        finally:
            shutil.rmtree(root_tmp, ignore_errors=True)
    finally:
        _restaurar_env(previo_env)


# =============================================================================
# CERTIFICACIÓN — ciclo analizar->evaluar->construir->iterar->certificar
# =============================================================================


def run_certificacion(
    nombre: str = "Proyecto",
    prompt: str = "",
    n8n_flows: Optional[List[Dict[str, Any]]] = None,
    reglas_negocio: Optional[List[str]] = None,
    objetivos: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    max_rondas: int = 4,
    incluir_dinamicas: bool = False,
    auto_fix: bool = True,
    min_confidence: float = 0.85,
    max_lines_per_fix: int = 40,
    enable_generic_fixer: bool = False,
    presupuesto_tokens: Optional[int] = None,
    presupuesto_usd: Optional[float] = None,
    openai_key: str = "",
    n8n_api_key: str = "",
    n8n_base_url: str = "",
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """Corre el ciclo completo de La Colmena sobre el proyecto: analiza, evalúa
    en todas las dimensiones, construye fixes (self-heal), re-evalúa e itera
    hasta CONVERGER, y emite un CERTIFICADO consciente de cobertura.

    Igual que self-heal, opera sobre un proyecto temporal efímero (nunca un repo
    real): lo único que el self-heal puede tocar es ese directorio descartable.
    """
    from juez.colmena.orquestador import certificar_proyecto

    n8n_flows = n8n_flows or []
    prompt = (prompt or "").strip()
    if not prompt and not n8n_flows:
        raise ValueError("La certificación necesita al menos un 'prompt' o un flujo n8n.")

    progress = progress_cb or _progress_noop
    previo_env = _setear_env_temporal(openai_key=openai_key, n8n_api_key=n8n_api_key, n8n_base_url=n8n_base_url)
    try:
        progress("Preparando componentes del proyecto", 5)
        componentes_n8n: List[tuple] = []
        for flow in n8n_flows:
            try:
                wf, _webhook, wf_nombre = _resolve_n8n_flow(flow, n8n_api_key, n8n_base_url)
                componentes_n8n.append((wf_nombre, wf))
            except Exception:
                pass

        root_tmp = _construir_proyecto_temporal(prompt, componentes_n8n, reglas_negocio, objetivos)
        try:
            progress("Ejecutando ciclo analizar->evaluar->construir->iterar", 30)
            certificado = certificar_proyecto(
                root_tmp,
                max_rondas=max_rondas,
                incluir_dinamicas=incluir_dinamicas,
                auto_fix=auto_fix,
                min_confidence=min_confidence,
                max_lines_per_fix=max_lines_per_fix,
                enable_generic_fixer=enable_generic_fixer,
                presupuesto_tokens=presupuesto_tokens,
                presupuesto_usd=presupuesto_usd,
                output_dir=root_tmp / "_cert_output",
            )
            certificado["nombre"] = nombre or "Proyecto"
            progress("Listo", 100)
            return certificado
        finally:
            shutil.rmtree(root_tmp, ignore_errors=True)
    finally:
        _restaurar_env(previo_env)
