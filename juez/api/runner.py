"""Runners no-interactivos para los evaluadores del Juez.

Estos wrappers ejecutan exactamente la misma lógica que `evaluar_elevenlabs.py`,
`evaluar_n8n.py` y `evaluar_pipeline.py`, pero sin prompts interactivos.
Reciben todos los parámetros como argumentos y retornan un dict serializable.

La carga de los módulos se hace dinámicamente con `importlib.util` para no
forzar refactor de los scripts existentes.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Ruta al root del proyecto (donde están evaluar_*.py)
_ROOT = Path(__file__).resolve().parent.parent


# =============================================================================
# CARGA DINÁMICA DE MÓDULOS
# =============================================================================

_modules_cache: Dict[str, Any] = {}


def _load_module(name: str) -> Any:
    """Carga un módulo evaluar_*.py por nombre, con caché."""
    if name in _modules_cache:
        return _modules_cache[name]
    path = _ROOT / f"{name}.py"
    if not path.exists():
        raise FileNotFoundError(f"No se encontró {path}")
    # Asegurar que el root esté en sys.path para imports relativos del módulo
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

    # Caso 2: URL o ID — descargar via API
    url_o_id = flow_dict.get("url") or flow_dict.get("workflow_id")
    if not url_o_id:
        raise ValueError(
            "Cada flujo n8n debe traer 'json_content', 'url' o 'workflow_id'"
        )

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
    """Evalúa un branch de ElevenLabs.

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
        # pero usando la config descargada con el branch_id (que sí refleja la rama).
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
    """Evalúa un agente cuando ya tenemos la config descargada (vía branch).

    Variante de run_elevenlabs_single que no re-descarga el agente sino que
    usa el agent_config ya obtenido del branch.
    """
    previo_env = _setear_env_temporal(openai_key=openai_key, elevenlabs_key=elevenlabs_key)
    try:
        mod = _load_module("evaluar_elevenlabs")
        eleven_key = elevenlabs_key or os.getenv("ELEVENLABS_API_KEY", "")
        nombre = agent_config.get("name", agent_id)

        progress(f"Análisis estático: {nombre} (branch {branch_context['branch_name']})", 30)
        analisis = mod.ElevenLabsAnalyzer(agent_config).analizar()
        # Inyectar contexto del branch en el análisis para que salga en el reporte
        analisis["branch_context"] = branch_context

        gpt_result: Dict[str, Any] = {}
        oai_key = openai_key or os.getenv("OPENAI_API_KEY", "")
        if oai_key:
            progress("Análisis profundo con GPT", 45)
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
                reporte_ca = f"\n[CONTRA-AGENTE — Error: {exc}]\n"

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
    # Nuevos parámetros (T-23):
    target_id: Optional[str] = None,
    include_n8n_flows: bool = True,
    n8n_api_key: str = "",
    n8n_base_url: str = "",
) -> Dict[str, Any]:
    """Evalúa un recurso de ElevenLabs.

    `target_id` (o `agent_id` como alias) puede ser:
      - agent_*    : se evalúa el agente directo (versión live).
      - agtbrch_*  : se resuelve al agente padre, se evalúa la config del
                     agente bajo esa rama. Si include_n8n_flows=True, además
                     se detectan y evalúan los flujos n8n que llama, y se
                     retorna un resultado tipo pipeline.

    Equivalente programático a `python evaluar_elevenlabs.py <ID>`.
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
            f"Los version IDs ({resolved}) no se evalúan directamente. "
            "Pásame el branch_id (agtbrch_*) o el agent_id (agent_*)."
        )
    if tipo == "unknown":
        raise ValueError(
            f"ID '{resolved}' no tiene un prefijo reconocido "
            "(esperado: agent_* o agtbrch_*)."
        )

    # tipo == "agent" — flujo original intacto
    progress = progress_cb or _progress_noop
    previo_env = _setear_env_temporal(openai_key=openai_key, elevenlabs_key=elevenlabs_key)
    try:
        progress("Cargando módulo ElevenLabs", 5)
        mod = _load_module("evaluar_elevenlabs")

        eleven_key = elevenlabs_key or os.getenv("ELEVENLABS_API_KEY", "")
        if not eleven_key:
            raise ValueError("ELEVENLABS_API_KEY no configurada")

        agent_id_real = resolved
        progress("Descargando agente de ElevenLabs", 15)
        client = mod.ElevenLabsClient(eleven_key)
        data = client.obtener_agente(agent_id_real)
        nombre = data.get("name", agent_id_real)

        progress(f"Análisis estático: {nombre}", 30)
        analisis = mod.ElevenLabsAnalyzer(data).analizar()

        gpt_result: Dict[str, Any] = {}
        oai_key = openai_key or os.getenv("OPENAI_API_KEY", "")
        if oai_key:
            progress("Análisis profundo con GPT", 45)
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
                reporte_ca = f"\n[CONTRA-AGENTE — Error: {exc}]\n"

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
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """Evalúa un único flujo n8n.

    `flow` es un dict tipo `N8nFlowSource`: {url, workflow_id, json_content, webhook_url}.
    """
    progress = progress_cb or _progress_noop
    previo_env = _setear_env_temporal(
        openai_key=openai_key, n8n_api_key=n8n_api_key, n8n_base_url=n8n_base_url
    )
    try:
        progress("Resolviendo origen del flujo n8n", 5)
        wf, webhook_url, nombre = _resolve_n8n_flow(flow, n8n_api_key, n8n_base_url)

        progress(f"Cargando módulo n8n", 10)
        mod = _load_module("evaluar_n8n")

        progress(f"Análisis estático: {nombre}", 25)
        analisis = mod.N8nAnalyzer(wf).analizar()

        gpt_result: Dict[str, Any] = {}
        oai_key = openai_key or os.getenv("OPENAI_API_KEY", "")
        if oai_key:
            progress("Análisis profundo con GPT", 40)
            try:
                gpt_result = mod.analizar_con_gpt(analisis, nombre)
                analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
            except Exception:
                pass

            progress("Validación dinámica de modelos LLM", 50)
            try:
                analisis = mod.validar_y_enriquecer_modelos(analisis, oai_key)
            except Exception:
                pass

        # Contra-agente
        batch_result = None
        reporte_ca = ""
        webhook_activo_msg = ""
        if total_conversaciones > 0 and webhook_url and oai_key:
            # T-14: verificar webhook activo antes de gastar GPT
            pipeline_mod = _load_module("evaluar_pipeline")
            activo, msg = pipeline_mod._verificar_webhook_activo(webhook_url)
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
                    )
                except Exception as exc:
                    reporte_ca = f"\n[CONTRA-AGENTE — Error: {exc}]\n"
            else:
                reporte_ca = (
                    "\n[CONTRA-AGENTE] Webhook no activo: " + msg +
                    "\nActiva el flujo en n8n para correr las pruebas dinámicas.\n"
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

        progress("Generando reporte", 95)
        archivo_origen = flow.get("url") or flow.get("workflow_id") or "(JSON inline via API)"
        reporte = mod.generar_reporte(analisis, gpt_result, nombre, archivo_origen)
        if reporte_ca:
            reporte = reporte + "\n\n" + reporte_ca
        if artef and artef.get("reporte"):
            reporte = reporte + "\n\n" + artef["reporte"]

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
    openai_key: str = "",
    elevenlabs_key: str = "",
    n8n_api_key: str = "",
    n8n_base_url: str = "",
    progress_cb: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """Evalúa un pipeline completo: 0..N agentes ElevenLabs + 0..M flujos n8n.

    Replica la lógica de `evaluar_pipeline.py main()` sin el menú interactivo.
    """
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

        progress("Cargando módulos", 3)
        pipeline_mod = _load_module("evaluar_pipeline")
        n8n_mod = _load_module("evaluar_n8n")
        eleven_mod = _load_module("evaluar_elevenlabs")

        total_nodos = len(eleven_ids) + len(n8n_flows)
        previews: List[Dict[str, Any]] = []
        flujos_fallidos: List[Dict[str, str]] = []

        # ── Fase 1: ElevenLabs ────────────────────────────────────────────────
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

        # ── Fase 1: n8n ──────────────────────────────────────────────────────
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
                "No se pudo analizar ningún nodo. Errores: "
                + "; ".join(f"{f['url']}: {f['error']}" for f in flujos_fallidos)
            )

        # ── Fase 2: pruebas dinámicas ────────────────────────────────────────
        node_results: List[Any] = []
        for i, pv in enumerate(previews):
            pct = 60 + int((i / max(len(previews), 1)) * 25)
            progress(f"Probando {pv['nombre']} ({i + 1}/{len(previews)})", pct)
            try:
                result = pipeline_mod._evaluar_con_analisis(
                    pv, oai_key, total_conversaciones, concurrencia
                )
                node_results.append(result)
            except Exception as exc:
                flujos_fallidos.append({"url": pv["nombre"], "etapa": "pruebas", "error": str(exc)})

        if not node_results:
            detalles = "; ".join(
                f"{f['url']} [{f['etapa']}]: {f['error']}" for f in flujos_fallidos
            ) or "(sin detalles)"
            raise RuntimeError(
                f"No se pudo evaluar ningún nodo en la fase dinámica. Errores: {detalles}"
            )

        # ── Grafo del pipeline ───────────────────────────────────────────────
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

        # ── Análisis de coherencia ───────────────────────────────────────────
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

        # ── Score final del pipeline ─────────────────────────────────────────
        scores_pipeline = pipeline_mod.calcular_score_pipeline(node_results, analisis_coherencia)
        pipeline_name = nombre or f"Pipeline ({', '.join(r.name for r in node_results[:2])})"

        # ── Reporte ──────────────────────────────────────────────────────────
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
            reporte_pipeline = f"\n[REPORTE DEL PIPELINE — Error: {exc}]\n"

        # Sección de flujos fallidos (si aplica)
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

        reporte_path = _guardar_reporte_txt(pipeline_name, reporte_pipeline, "pipeline")

        # ── Resultado serializable ──────────────────────────────────────────
        nodos_out: List[Dict[str, Any]] = []
        for r in node_results:
            nodos_out.append({
                "node_id": r.node_id,
                "node_type": r.node_type,
                "name": r.name,
                "score_general": r.scores.get("score_general", 0.0),
                "scores": _strip_non_serializable(r.scores),
                "problemas": _strip_non_serializable(r.analisis.get("problemas", [])),
                "trigger": _strip_non_serializable(r.analisis.get("trigger", {})) if r.node_type == "n8n" else None,
                "dynamic_tests_ran": r.dynamic_tests_ran,
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
        }
    finally:
        _restaurar_env(previo_env)
