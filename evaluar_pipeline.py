"""evaluar_pipeline.py — Evaluador de pipelines multi-agente.

Acepta múltiples agentes ElevenLabs y/o flujos n8n, detecta automáticamente
sus conexiones, evalúa cada nodo individualmente y produce un reporte unificado
del pipeline completo.

Uso:
    python evaluar_pipeline.py --eleven agent_id_1 agent_id_2
    python evaluar_pipeline.py --n8n flujo1.json flujo2.json
    python evaluar_pipeline.py --eleven agent_id_1 --n8n flujo2.json flujo3.json --nombre "Pipeline Lía"
    python evaluar_pipeline.py --eleven id1 --n8n f.json --ci-mode --ci-threshold 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Carga de .env ─────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ── Rich (opcional) ───────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.panel import Panel
    console = Console(highlight=False, emoji=False)
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ── Módulos Juez ──────────────────────────────────────────────────────────────
try:
    from evaluation.contra_agente.generator import generar_batch
    from evaluation.contra_agente.worker import ConversationWorker
    from evaluation.contra_agente.evaluator import TurnEvaluator
    from evaluation.contra_agente.reporter import generar_reporte_batch
    from evaluation.contra_agente.models import ConversationBatch

    def _ejecutar_batch(batch, adapter_factory, evaluator, openai_key="", concurrencia=3):
        """Ejecuta un batch de conversaciones con concurrencia."""
        import concurrent.futures
        results = []
        def _run_one(plan):
            adapter = adapter_factory("n8n", plan.agent_id)
            worker = ConversationWorker(plan, adapter, evaluator, openai_key=openai_key)
            return worker.run()
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrencia) as ex:
            futures = {ex.submit(_run_one, p): p for p in batch.plans}
            for fut in concurrent.futures.as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as exc:
                    pass
        from evaluation.contra_agente.models import BatchResult
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        by_cat: Dict = {}
        for r in results:
            cat = r.category
            if cat not in by_cat:
                by_cat[cat] = {"total": 0, "passed": 0, "pass_rate": 0.0}
            by_cat[cat]["total"] += 1
            if r.passed:
                by_cat[cat]["passed"] += 1
        for cat in by_cat:
            t = by_cat[cat]["total"]
            by_cat[cat]["pass_rate"] = round(by_cat[cat]["passed"] / t, 3) if t else 0.0
        scorecard = {}
        all_scores: Dict[str, List[float]] = {}
        for r in results:
            for tr in r.turn_results:
                for metric, val in tr.scores.items():
                    all_scores.setdefault(metric, []).append(val)
        for metric, vals in all_scores.items():
            scorecard[metric] = round(sum(vals) / len(vals), 3)
        return BatchResult(
            batch_id=batch.batch_id,
            agent_id=batch.agent_id,
            total=total,
            passed=passed,
            failed=total - passed,
            pass_rate=round(passed / total, 3) if total else 0.0,
            by_category=by_cat,
            collapse_pattern={},
            results=results,
            recommendations=[],
            scorecard=scorecard,
        )

    HAS_CA = True
except ImportError as _e:
    HAS_CA = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _print_ok(msg: str) -> None:
    if HAS_RICH:
        console.print(f"[green]OK[/green] {msg}")
    else:
        print(f"OK  {msg}")

def _print_err(msg: str) -> None:
    if HAS_RICH:
        console.print(f"[red]ERROR[/red] {msg}")
    else:
        print(f"ERROR  {msg}")

def _print_info(msg: str) -> None:
    if HAS_RICH:
        console.print(f"[cyan]>[/cyan] {msg}")
    else:
        print(f">  {msg}")

def _spin(msg: str, fn, *args, **kwargs):
    if HAS_RICH:
        with Progress(SpinnerColumn(), TextColumn(f"[cyan]{msg}"), console=console, transient=True) as p:
            p.add_task("", total=None)
            return fn(*args, **kwargs)
    print(f"  {msg}")
    return fn(*args, **kwargs)


# ── Resultado de evaluación individual ───────────────────────────────────────

class NodeResult:
    """Resultado completo de evaluar un nodo del pipeline."""
    def __init__(
        self,
        node_id: str,
        node_type: str,
        name: str,
        analisis: Dict,
        scores: Dict,
        batch_result: Any,
        reporte_texto: str,
        raw_flow: Optional[Dict] = None,
        dynamic_tests_ran: bool = False,
    ):
        self.node_id     = node_id
        self.node_type   = node_type
        self.name        = name
        self.analisis    = analisis
        self.scores      = scores
        self.batch_result = batch_result
        self.reporte_texto = reporte_texto
        self.raw_flow    = raw_flow   # para n8n: JSON original
        # True solo cuando el contra-agente realmente se ejecutó (no cuando se omitió
        # por falta de webhook o por --skip-contra-agente). Permite distinguir en el
        # reporte "score estático" vs "score con pruebas dinámicas".
        self.dynamic_tests_ran = dynamic_tests_ran


# ── Revisión interactiva de reglas ────────────────────────────────────────────

def _mostrar_reglas_resumen(reglas: Dict, analisis: Dict) -> None:
    """Imprime un resumen compacto de las reglas de negocio de un nodo."""
    dominio     = reglas.get("dominio") or analisis.get("dominio", "")
    enfoque     = reglas.get("enfoque") or analisis.get("descripcion", "")
    no_puede    = reglas.get("no_puede", [])
    reglas_clave = reglas.get("reglas_clave", [])
    casos_limite = reglas.get("casos_limite_criticos", [])
    instrucciones = reglas.get("instrucciones_extra", "")

    if dominio:
        print(f"\n  Dominio    : {dominio}")
    if enfoque:
        # Truncar a 100 chars para no saturar
        print(f"  Enfoque    : {enfoque[:100]}{'...' if len(enfoque) > 100 else ''}")
    if no_puede:
        print(f"\n  Prohibiciones ({len(no_puede)}):")
        for i, r in enumerate(no_puede, 1):
            print(f"    {i}. {r}")
    if reglas_clave:
        print(f"\n  Reglas clave ({len(reglas_clave)}):")
        for i, r in enumerate(reglas_clave, 1):
            print(f"    {i}. {r}")
    if casos_limite:
        print(f"\n  Casos limite ({len(casos_limite)}):")
        for i, r in enumerate(casos_limite, 1):
            print(f"    {i}. {r}")
    if instrucciones:
        print(f"\n  Directrices : {instrucciones[:80]}{'...' if len(instrucciones) > 80 else ''}")


def _revisar_nodo(nombre: str, analisis: Dict, openai_key: str) -> Dict:
    """Abre la sesión de revisión conversacional para UN nodo y retorna el análisis
    actualizado. El usuario escribe ajustes en lenguaje natural; Enter en blanco cierra."""
    SEP = "-" * 60
    reglas = analisis.get("reglas_negocio", {})

    print()
    print(SEP)
    print(f"  AJUSTANDO: {nombre}")
    print(SEP)
    _mostrar_reglas_resumen(reglas, analisis)
    print()
    print("  Escribe tus ajustes (Enter en blanco para volver al menu).")
    print("  Ejemplos: 'agrega que no puede dar precios'")
    print("            'quita la prohibicion 2'")
    print("            'enfocate mas en casos de devolucion'")
    print()

    if not reglas or not openai_key:
        # Sin GPT: acumular ajustes como instrucciones_extra
        while True:
            try:
                ajuste = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not ajuste:
                break
            instrucciones = reglas.get("instrucciones_extra", "")
            reglas["instrucciones_extra"] = (instrucciones + " " + ajuste).strip()
            analisis["reglas_negocio"] = reglas
            print("  (Registrado)")
        return analisis

    # Con GPT: loop conversacional completo
    try:
        from evaluation.utils.review import revisar_reglas_negocio as _revisar
        reglas_actualizadas = _revisar(reglas, openai_key=openai_key)
        analisis["reglas_negocio"] = reglas_actualizadas
    except Exception as exc:
        print(f"  (Revision sin GPT: {exc})")
        while True:
            try:
                ajuste = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not ajuste:
                break
            instrucciones = reglas.get("instrucciones_extra", "")
            reglas["instrucciones_extra"] = (instrucciones + " " + ajuste).strip()
            analisis["reglas_negocio"] = reglas
            print("  (Registrado)")

    return analisis


def _menu_revision_pipeline(nodos: List[Dict], openai_key: str) -> List[Dict]:
    """Muestra el menú persistente de revisión de todos los nodos del pipeline.

    nodos: lista de dicts con {"nombre": str, "analisis": Dict, "ajustado": bool}
    Retorna la lista con los análisis posiblemente modificados.
    El menú solo cierra cuando el usuario confirma con Enter en blanco.
    """
    SEP = "=" * 60

    while True:
        # ── Limpiar y redibujar el menú ───────────────────────────────────
        print()
        print(SEP)
        print("  REVISION DEL PIPELINE — selecciona un flujo para ajustar")
        print(SEP)
        print()

        for i, nodo in enumerate(nodos, 1):
            nombre   = nodo["nombre"]
            analisis = nodo["analisis"]
            reglas   = analisis.get("reglas_negocio", {})
            dominio  = reglas.get("dominio") or analisis.get("dominio", "")
            n_reglas = len(reglas.get("reglas_clave", []))
            n_prohib = len(reglas.get("no_puede", []))
            ajustado = "[ajustado]" if nodo.get("ajustado") else ""
            tipo     = nodo.get("tipo", "")
            tipo_str = f"[{tipo}] " if tipo else ""

            print(
                f"  [{i}] {tipo_str}{nombre:<35} "
                f"{dominio[:25]:<25}  "
                f"{n_prohib} prohib. {n_reglas} reglas  {ajustado}"
            )

        print()
        print("  Escribe el numero del flujo a ajustar.")
        print("  Enter en blanco cuando estes listo para iniciar el analisis.")
        print()

        try:
            seleccion = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        # Enter en blanco → confirmar salida
        if not seleccion:
            print()
            print("  Iniciando analisis con la configuracion actual...")
            print(SEP)
            print()
            break

        # Validar que sea un número válido
        if not seleccion.isdigit() or not (1 <= int(seleccion) <= len(nodos)):
            print(f"  Opcion invalida. Ingresa un numero entre 1 y {len(nodos)}.")
            continue

        idx = int(seleccion) - 1
        nodo = nodos[idx]

        # Abrir revisión para ese nodo
        nodo["analisis"] = _revisar_nodo(nodo["nombre"], nodo["analisis"], openai_key)
        nodo["ajustado"] = True
        # Vuelve automáticamente al menú (el while True redibuja)

    return nodos


def _revisar_antes_de_pruebas(analisis: Dict, nombre: str, openai_key: str) -> Dict:
    """Wrapper de compatibilidad para revisión individual (usado en nodo único)."""
    nodos = [{"nombre": nombre, "analisis": analisis, "tipo": "", "ajustado": False}]
    resultado = _menu_revision_pipeline(nodos, openai_key)
    return resultado[0]["analisis"]


# ── Evaluación dinámica a partir de análisis ya hecho ─────────────────────────

def _verificar_webhook_activo(webhook_url: str, timeout: float = 6.0) -> Tuple[bool, str]:
    """Envía una probe POST al webhook para detectar si el flujo está activo en n8n.

    Returns (activo, mensaje_diagnostico).
    - activo=True  → el webhook responde, se pueden correr pruebas.
    - activo=False → el flujo está inactivo, no vale la pena correr pruebas.

    La probe es idéntica a lo que enviaría el contra-agente, así que no es invasiva.
    """
    try:
        import requests as _req
    except ImportError:
        return True, ""  # Sin requests: asumir activo y dejar que falle después

    try:
        resp = _req.post(
            webhook_url,
            json={"message": "verificacion", "sessionId": "_juez_probe"},
            headers={"Content-Type": "application/json"},
            timeout=timeout,
            allow_redirects=True,
        )

        if resp.status_code == 404:
            return False, (
                "El flujo no esta activado en n8n (HTTP 404). "
                "Activa el toggle 'Active' en el editor del flujo antes de correr pruebas."
            )

        # Intentar leer el body para distinguir casos
        try:
            body = resp.json()
        except Exception:
            body = {}

        if isinstance(body, dict):
            msg = str(body.get("message", "")).strip()
            if "could not be started" in msg.lower():
                return False, (
                    "El flujo esta desactivado en n8n. "
                    "Activa el toggle 'Active' en el editor antes de correr pruebas."
                )
            if msg == "Workflow was started":
                # Webhook existe y el flujo está activo, pero sin "Respond to Webhook"
                # Las pruebas correrán pero las respuestas serán parciales
                return True, (
                    "ADVERTENCIA: El flujo acepta requests pero no tiene nodo "
                    "'Respond to Webhook' — las pruebas conversacionales tendran "
                    "respuestas vacias o incompletas."
                )

        # Cualquier otra respuesta HTTP (200, 405, 500, etc.) → existe el webhook
        return True, ""

    except Exception as exc:
        # No se pudo conectar — intentar igual y reportar
        return True, f"No se pudo verificar el webhook previamente ({type(exc).__name__}): {exc}"


def _evaluar_con_analisis(
    preview: Dict,
    openai_key: str,
    total_conv: int,
    concurrencia: int,
) -> NodeResult:
    """Recibe un preview con analisis ya hecho (y opcionalmente ajustado por el usuario)
    y solo corre la parte dinámica (contra-agente). Retorna NodeResult completo."""
    import importlib.util as _ilu

    tipo      = preview["tipo"]
    nombre    = preview["nombre"]
    analisis  = preview["analisis"]
    gpt_result = preview.get("gpt_result", {})
    agent_id  = preview.get("agent_id", "")
    webhook   = preview.get("webhook", "")
    wf_data   = preview.get("wf_data")
    json_path = preview.get("json_path", "")
    node_id   = nombre.lower().replace(" ", "_")[:40]

    batch_result = None
    reporte_ca   = ""

    if tipo == "elevenlabs":
        _spec = _ilu.spec_from_file_location("evaluar_elevenlabs", Path(__file__).parent / "evaluar_elevenlabs.py")
        _mod  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
        elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")

        if HAS_CA and total_conv > 0:
            from evaluation.contra_agente.generator import generar_batch as _gen
            from evaluation.contra_agente.evaluator import TurnEvaluator as _TE
            from evaluation.contra_agente.reporter import generar_reporte_batch as _rep
            from evaluation.contra_agente.adapters.elevenlabs import ElevenLabsAdapter

            batch = _gen(analisis=analisis, agent_name=nombre, total=total_conv,
                         concurrency=concurrencia, adapter="elevenlabs", openai_key=openai_key)
            def _af(_t, _id):
                return ElevenLabsAdapter(
                    agent_id=_id or agent_id,
                    analisis=analisis,
                    openai_key=openai_key,
                    el_key=elevenlabs_key,
                )
            evaluator    = _TE(openai_key=openai_key)
            batch_result = _ejecutar_batch(batch, _af, evaluator, openai_key=openai_key, concurrencia=concurrencia)
            reporte_ca   = _rep(batch_result, agent_name=nombre)

        scores = _mod.calcular_scorecard(analisis, batch_result)
        reporte_texto = _mod.generar_reporte(analisis, gpt_result, nombre, agent_id,
                                              juez_report=None, scores_precalculados=scores)
        if reporte_ca:
            reporte_texto += "\n\n" + reporte_ca

        return NodeResult(node_id=agent_id or node_id, node_type="elevenlabs",
                          name=nombre, analisis=analisis, scores=scores,
                          batch_result=batch_result, reporte_texto=reporte_texto,
                          dynamic_tests_ran=(batch_result is not None))

    else:  # n8n
        _spec = _ilu.spec_from_file_location("evaluar_n8n", Path(__file__).parent / "evaluar_n8n.py")
        _mod  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)

        if HAS_CA and total_conv > 0 and webhook:
            # T-14: Verificar si el webhook está activo antes de gastar llamadas GPT
            _wh_activo, _wh_msg = _verificar_webhook_activo(webhook)
            if not _wh_activo:
                SEP80 = "=" * 80
                reporte_ca = "\n".join([
                    "",
                    SEP80,
                    f"  PRUEBAS DEL AGENTE: {nombre}",
                    SEP80,
                    "",
                    "  RESULTADO GENERAL",
                    "  " + "-" * 17,
                    "",
                    "  Pruebas dinamicas NO ejecutadas.",
                    "",
                    f"  Razon: {_wh_msg}",
                    "",
                    "  Para habilitar las pruebas dinamicas:",
                    "    1. Abre el flujo en n8n",
                    "    2. Activa el toggle 'Active' en la esquina superior derecha",
                    "    3. Verifica que el nodo Webhook tenga un path configurado",
                    "    4. Vuelve a correr el evaluador",
                    "",
                    SEP80,
                ])
            else:
                if _wh_msg:
                    # Advertencia (ej: sin Respond to Webhook) — correr igual pero avisar
                    print(f"  AVISO ({nombre}): {_wh_msg}")
                batch_result, reporte_ca = _mod.ejecutar_contra_agente(
                    analisis_n8n=analisis, webhook_url=webhook,
                    agent_name=nombre, total_conv=total_conv, concurrencia=concurrencia,
                )
        elif HAS_CA and total_conv > 0 and not webhook:
            # No hay webhook — generar sección explicativa usando info del trigger
            trigger_info = analisis.get("trigger", {})
            trigger_label = trigger_info.get("label", "trigger no detectado")
            trigger_tipo = trigger_info.get("tipo", "desconocido")
            instrucciones = trigger_info.get("instrucciones", [])

            SEP80 = "=" * 80
            lineas_ca = [
                "",
                SEP80,
                f"  PRUEBAS DEL AGENTE: {nombre}",
                SEP80,
                "",
                "  RESULTADO GENERAL",
                "  " + "-" * 17,
                "",
                "  Pruebas dinamicas NO ejecutadas.",
                "",
                f"  Razon  : Este flujo usa trigger '{trigger_label}' (no webhook HTTP).",
                f"  Tipo   : {trigger_tipo}",
                "",
            ]

            if instrucciones:
                lineas_ca.append("  Para evaluar este flujo dinamicamente:")
                for instr in instrucciones:
                    lineas_ca.append(f"    {instr}")
                lineas_ca.append("")
            else:
                lineas_ca += [
                    "  Activa el flujo en n8n y configura un webhook de entrada",
                    "  para habilitar las pruebas conversacionales del Juez.",
                    "",
                ]

            lineas_ca.append(
                "  El analisis estatico (estructura, seguridad, prompts) SI se realizo"
            )
            lineas_ca.append(
                "  y esta reflejado en el score y el reporte de arriba."
            )
            lineas_ca.append("")
            lineas_ca.append(SEP80)
            reporte_ca = "\n".join(lineas_ca)

        scores = _mod.calcular_score_n8n(analisis, batch_result)
        ruta_str = json_path or f"[n8n-api:{wf_data.get('id', 'unknown')}]" if wf_data else nombre
        reporte_texto = _mod.generar_reporte(analisis, gpt_result, nombre, ruta_str)
        if reporte_ca:
            reporte_texto += "\n\n" + reporte_ca

        return NodeResult(node_id=node_id, node_type="n8n",
                          name=nombre, analisis=analisis, scores=scores,
                          batch_result=batch_result, reporte_texto=reporte_texto,
                          raw_flow=wf_data,
                          dynamic_tests_ran=(batch_result is not None))


# ── Evaluación de nodo ElevenLabs ─────────────────────────────────────────────

def _evaluar_elevenlabs(agent_id: str, openai_key: str, total_conv: int, concurrencia: int) -> NodeResult:
    """Descarga y evalúa un agente ElevenLabs completo."""
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "evaluar_elevenlabs",
        Path(__file__).parent / "evaluar_elevenlabs.py"
    )
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not elevenlabs_key:
        raise ValueError("ELEVENLABS_API_KEY no configurada en .env")

    client   = mod.ElevenLabsClient(elevenlabs_key)
    data     = client.obtener_agente(agent_id)
    name     = data.get("name", agent_id)
    analisis = mod.ElevenLabsAnalyzer(data).analizar()

    gpt_result: Dict = {}
    if openai_key:
        try:
            gpt_result = mod.analizar_con_gpt(analisis, name)
            analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
        except Exception:
            pass

    # ── Revisión interactiva antes de las pruebas dinámicas ──────────────────
    analisis = _revisar_antes_de_pruebas(analisis, name, openai_key)

    batch_result = None
    reporte_ca   = ""
    if HAS_CA and total_conv > 0:
        from evaluation.contra_agente.generator import generar_batch as _gen
        from evaluation.contra_agente.evaluator import TurnEvaluator as _TE
        from evaluation.contra_agente.reporter import generar_reporte_batch as _rep
        from evaluation.contra_agente.adapters.elevenlabs import ElevenLabsAdapter

        batch = _gen(
            analisis=analisis,
            agent_name=name,
            total=total_conv,
            concurrency=concurrencia,
            adapter="elevenlabs",
            openai_key=openai_key,
        )

        def _af(_type, _id):
            return ElevenLabsAdapter(
                agent_id=_id or agent_id,
                analisis=analisis,
                openai_key=openai_key,
                el_key=elevenlabs_key,
            )

        evaluator    = _TE(openai_key=openai_key)
        batch_result = _ejecutar_batch(batch, _af, evaluator, openai_key=openai_key, concurrencia=concurrencia)
        reporte_ca   = _rep(batch_result, agent_name=name)

    scores = mod.calcular_scorecard(analisis, batch_result)
    reporte_texto = mod.generar_reporte(analisis, gpt_result, name, agent_id,
                                        juez_report=None, scores_precalculados=scores)
    if reporte_ca:
        reporte_texto += "\n\n" + reporte_ca

    return NodeResult(
        node_id=agent_id,
        node_type="elevenlabs",
        name=name,
        analisis=analisis,
        scores=scores,
        batch_result=batch_result,
        reporte_texto=reporte_texto,
    )


# ── Descarga de flujo n8n via API ─────────────────────────────────────────────

def _parsear_url_workflow(url_o_id: str) -> Tuple[str, str]:
    """Extrae (base_url, workflow_id) de una URL de n8n o de un ID puro.

    Acepta cualquiera de estos formatos:
      - URL completa : https://n8n-dev.lambdaanalytics.co/workflow/Vdc5Ctfv9Xe3AuAt
      - Solo el ID   : Vdc5Ctfv9Xe3AuAt

    Retorna ("", id) si es un ID puro (base_url deberá venir del .env).
    """
    s = url_o_id.strip()
    if s.startswith("http://") or s.startswith("https://"):
        # Buscar /workflow/{id} en la URL
        import re
        m = re.search(r"/workflow/([^/?#]+)", s)
        if m:
            wf_id = m.group(1)
            # base_url = esquema + host (sin path)
            from urllib.parse import urlparse
            p = urlparse(s)
            base = f"{p.scheme}://{p.netloc}"
            return base, wf_id
        raise ValueError(
            f"No se pudo extraer el ID del flujo de la URL: {s}\n"
            "  Formato esperado: https://tu-n8n.com/workflow/WORKFLOW_ID"
        )
    # Es un ID puro
    return "", s


def _descargar_workflow_n8n(
    base_url: str,
    api_key: str,
    workflow_id: str,
) -> Dict[str, Any]:
    """Descarga el JSON completo de un flujo n8n usando su ID y la API de n8n.

    Llama a GET {base_url}/api/v1/workflows/{workflow_id}
    con el header X-N8N-API-KEY.
    """
    try:
        import requests as _req
    except ImportError:
        raise RuntimeError(
            "Instala 'requests' para descargar flujos n8n: pip install requests"
        )

    url = f"{base_url.rstrip('/')}/api/v1/workflows/{workflow_id}"
    resp = _req.get(url, headers={"X-N8N-API-KEY": api_key}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _extraer_webhook_url(wf: Dict[str, Any], base_url: str) -> str:
    """Extrae la URL del webhook del JSON del flujo n8n.

    Busca el primer nodo de tipo 'n8n-nodes-base.webhook' y construye
    la URL completa: {base_url}/webhook/{path}
    """
    nodes = wf.get("nodes", [])
    for node in nodes:
        if node.get("type") == "n8n-nodes-base.webhook":
            path = node.get("parameters", {}).get("path", "")
            if path:
                path = path.lstrip("/")
                return f"{base_url.rstrip('/')}/webhook/{path}"
    return ""


# ── Evaluación de nodo n8n ────────────────────────────────────────────────────

def _evaluar_n8n(
    json_path: str,
    webhook_url: str,
    openai_key: str,
    total_conv: int,
    concurrencia: int,
    wf_override: Optional[Dict] = None,
) -> NodeResult:
    """Carga y evalúa un flujo n8n completo.

    Si wf_override está presente se usa ese dict directamente (flujo descargado
    via API) en lugar de leer desde json_path.
    """
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "evaluar_n8n",
        Path(__file__).parent / "evaluar_n8n.py"
    )
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if wf_override is not None:
        wf = wf_override
        ruta_str = f"[n8n-api:{wf.get('id', 'unknown')}]"
    else:
        ruta   = Path(json_path)
        wf     = json.loads(ruta.read_text(encoding="utf-8"))
        ruta_str = str(ruta)

    name    = wf.get("name", json_path)
    node_id = name.lower().replace(" ", "_")[:40]

    analisis = mod.N8nAnalyzer(wf).analizar()

    gpt_result: Dict = {}
    if openai_key:
        try:
            gpt_result = mod.analizar_con_gpt(analisis, name)
            analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
        except Exception:
            pass

    # ── Revisión interactiva antes de las pruebas dinámicas ──────────────────
    analisis = _revisar_antes_de_pruebas(analisis, name, openai_key)

    batch_result = None
    reporte_ca   = ""
    if HAS_CA and total_conv > 0 and webhook_url:
        batch_result, reporte_ca = mod.ejecutar_contra_agente(
            analisis_n8n=analisis,
            webhook_url=webhook_url,
            agent_name=name,
            total_conv=total_conv,
            concurrencia=concurrencia,
        )

    scores = mod.calcular_score_n8n(analisis, batch_result)
    reporte_texto = mod.generar_reporte(analisis, gpt_result, name, ruta_str)
    if reporte_ca:
        reporte_texto += "\n\n" + reporte_ca

    return NodeResult(
        node_id=node_id,
        node_type="n8n",
        name=name,
        analisis=analisis,
        scores=scores,
        batch_result=batch_result,
        reporte_texto=reporte_texto,
        raw_flow=wf,
    )


# ── Score del pipeline ────────────────────────────────────────────────────────

def calcular_score_pipeline(
    node_results: List[NodeResult],
    analisis_coherencia: Dict,
) -> Dict[str, Any]:
    """Score compuesto del pipeline."""
    if not node_results:
        return {"score_general": 0.0}

    scores_individuales = [r.scores.get("score_general", 0.0) for r in node_results]
    promedio_individual  = round(sum(scores_individuales) / len(scores_individuales), 1)
    score_coherencia     = analisis_coherencia.get("score_coherencia", promedio_individual)

    # Penalizar por gaps y nodo más débil
    n_gaps  = len(analisis_coherencia.get("gaps", []))
    penalty = min(n_gaps * 5, 20)
    score_coherencia = max(0.0, score_coherencia - penalty)

    score_final = round(promedio_individual * 0.60 + score_coherencia * 0.40, 1)

    # Nodos que solo tuvieron análisis estático (sin webhook activo)
    nodos_solo_estatico = [r.name for r in node_results if not r.dynamic_tests_ran]

    return {
        "score_general":        score_final,
        "score_promedio_nodos": promedio_individual,
        "score_coherencia":     round(score_coherencia, 1),
        "scores_por_nodo":      {r.node_id: r.scores.get("score_general", 0.0) for r in node_results},
        "punto_mas_debil":      analisis_coherencia.get("punto_mas_debil", ""),
        "latencia_estimada_ms": analisis_coherencia.get("latencia_estimada_ms", 0),
        # Lista de nodos evaluados solo con análisis estático (sin pruebas dinámicas).
        # Si está vacío, todos los nodos tuvieron pruebas dinámicas.
        "nodos_solo_estatico":  nodos_solo_estatico,
    }


# ── Historial y benchmark del pipeline ───────────────────────────────────────

def _persistir_pipeline(pipeline_name: str, scores: Dict, node_results: List[NodeResult]) -> Tuple[Optional[Dict], str, str]:
    """Guarda en historial y benchmark, retorna (anterior, comparacion, benchmark_sec)."""
    try:
        from evaluation.history import store as hist_store
        from evaluation.benchmark import store as bench_store

        pipeline_id = pipeline_name.lower().replace(" ", "_")[:40]
        analisis_dummy: Dict = {"problemas": []}
        snapshot = hist_store.build_snapshot(pipeline_id, pipeline_name, scores, analisis_dummy)
        hist_store.guardar(pipeline_id, snapshot)
        anterior = hist_store.cargar_anterior(pipeline_id)
        comparacion = hist_store.generar_seccion_comparacion(snapshot, anterior)
        bench_store.guardar_entrada(pipeline_id, pipeline_name, "pipeline", scores)
        benchmark_sec = bench_store.generar_seccion_benchmark(scores, domain="pipeline")
        return anterior, comparacion, benchmark_sec
    except Exception as exc:
        return None, f"\n  [Historial no disponible: {exc}]\n", ""


# ── Modo interactivo ──────────────────────────────────────────────────────────

def _modo_interactivo() -> Tuple[List[str], List[str]]:
    """Pide agentes por terminal cuando no se pasan argumentos.

    Retorna (eleven_ids, n8n_urls).
    El usuario ingresa URLs/IDs uno por línea y deja en blanco para continuar.
    """
    SEP = "-" * 60

    print()
    print(SEP)
    print("  MODO INTERACTIVO")
    print(SEP)
    print()
    print("  Ingresa las URLs de tus flujos n8n o IDs de agentes")
    print("  ElevenLabs. Deja en blanco y presiona Enter para")
    print("  iniciar el análisis.")
    print()

    # ── Flujos n8n ────────────────────────────────────────────────────────────
    n8n_urls: List[str] = []
    print("  FLUJOS N8N")
    print("  Pega la URL completa del flujo")
    print("  (ej: https://n8n-dev.lambdaanalytics.co/workflow/Vdc5Ctfv9Xe3AuAt)")
    print()
    idx = 1
    while True:
        try:
            val = input(f"  Flujo {idx}: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not val:
            break
        n8n_urls.append(val)
        idx += 1

    # ── Agentes ElevenLabs ────────────────────────────────────────────────────
    eleven_ids: List[str] = []
    print()
    print("  AGENTES ELEVENLABS  (opcional — Enter para omitir)")
    print("  Ingresa el Agent ID de ElevenLabs")
    print()
    idx = 1
    while True:
        try:
            val = input(f"  Agente {idx}: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not val:
            break
        eleven_ids.append(val)
        idx += 1

    # ── Resumen ───────────────────────────────────────────────────────────────
    print()
    print(SEP)
    total = len(n8n_urls) + len(eleven_ids)
    if total:
        if n8n_urls:
            print(f"  {len(n8n_urls)} flujo(s) n8n:")
            for u in n8n_urls:
                print(f"    · {u}")
        if eleven_ids:
            print(f"  {len(eleven_ids)} agente(s) ElevenLabs:")
            for e in eleven_ids:
                print(f"    · {e}")
        print()
        print(f"  Iniciando análisis de {total} nodo(s)...")
    print(SEP)
    print()

    return eleven_ids, n8n_urls


# ── Banner ────────────────────────────────────────────────────────────────────

def _banner() -> None:
    if HAS_RICH:
        console.print(Panel.fit(
            "[bold cyan]LAMBDA ANALYTICS[/bold cyan] [bold white]JUEZ[/bold white]\n"
            "[dim]Evaluador de Pipelines Multi-Agente[/dim]\n"
            "[dim]ElevenLabs · n8n · Detección automática de flujo[/dim]",
            border_style="cyan", padding=(1, 4),
        ))
    else:
        print("=" * 70)
        print("  LAMBDA ANALYTICS JUEZ — Evaluador de Pipelines Multi-Agente")
        print("=" * 70)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _banner()

    parser = argparse.ArgumentParser(prog="evaluar_pipeline.py", add_help=True)
    parser.add_argument("--eleven",         nargs="+", default=[], metavar="AGENT_ID",
                        help="IDs de agentes ElevenLabs a incluir en el pipeline")
    parser.add_argument("--n8n",            nargs="+", default=[], metavar="ARCHIVO.json",
                        help="Archivos JSON de flujos n8n a incluir en el pipeline")
    parser.add_argument("--webhooks",       nargs="+", default=[], metavar="URL",
                        help="URLs de webhook para los flujos n8n (en el mismo orden que --n8n)")
    # ── Integración directa con API de n8n ──────────────────────────────────
    parser.add_argument("--n8n-url",        default="", metavar="URL",
                        help="URL base de tu instancia n8n. Se extrae automáticamente si pasas URLs completas en --n8n-workflows")
    parser.add_argument("--n8n-api-key",    default="", metavar="API_KEY",
                        help="API key de n8n (X-N8N-API-KEY). También puede estar en .env como N8N_API_KEY")
    parser.add_argument("--n8n-workflows",  nargs="+", default=[], metavar="URL_O_ID",
                        help="URLs completas o IDs de flujos n8n. Ej: https://n8n.com/workflow/Vdc5Ctfv9Xe3AuAt")
    # ────────────────────────────────────────────────────────────────────────
    parser.add_argument("--nombre",         default="", metavar="NOMBRE",
                        help="Nombre del pipeline (default: auto-generado)")
    parser.add_argument("--total-conversaciones", type=int, default=5, metavar="N",
                        help="Conversaciones de contra-agente por nodo (default: 5)")
    parser.add_argument("--concurrencia",   type=int, default=3)
    parser.add_argument("--ci-mode",        action="store_true")
    parser.add_argument("--ci-threshold",   type=float, default=5.0)
    parser.add_argument("--skip-contra-agente", action="store_true",
                        help="Omitir el contra-agente (solo análisis estático)")
    args = parser.parse_args()

    # ── Modo interactivo: si no se pasó nada, pedir por terminal ─────────────
    if not args.eleven and not args.n8n and not args.n8n_workflows:
        args.eleven, args.n8n_workflows = _modo_interactivo()
        if not args.eleven and not args.n8n_workflows:
            _print_err("No se ingresó ningún agente. Abortando.")
            sys.exit(1)

    openai_key  = os.getenv("OPENAI_API_KEY", "")
    total_conv  = 0 if args.skip_contra_agente else args.total_conversaciones

    # ── Descargar flujos n8n via API ──────────────────────────────────────────
    n8n_api_key = args.n8n_api_key or os.getenv("N8N_API_KEY", "")

    # Flujos descargados via API: lista de (wf_dict, webhook_url)
    n8n_from_api: List[tuple] = []
    # Registro de flujos que fallaron (para incluirlos en el reporte)
    flujos_fallidos: List[Dict[str, str]] = []

    if args.n8n_workflows:
        if not n8n_api_key:
            _print_err("--n8n-workflows requiere --n8n-api-key (o N8N_API_KEY en .env)")
            sys.exit(1)
        _print_info(f"Descargando {len(args.n8n_workflows)} flujo(s) desde n8n API...")
        for url_o_id in args.n8n_workflows:
            try:
                # Extraer base_url e ID automáticamente de la URL
                base_url_extraida, wf_id = _parsear_url_workflow(url_o_id)
                # base_url: URL extraída > --n8n-url > .env
                base_url = base_url_extraida or args.n8n_url or os.getenv("N8N_BASE_URL", "")
                if not base_url:
                    raise ValueError(
                        "No se pudo determinar la URL base. Pasa la URL completa "
                        "(https://tu-n8n.com/workflow/ID) o configura N8N_BASE_URL en .env"
                    )
                wf_data = _descargar_workflow_n8n(base_url, n8n_api_key, wf_id)
                webhook = _extraer_webhook_url(wf_data, base_url)
                n8n_from_api.append((wf_data, webhook))
                wf_name = wf_data.get("name", wf_id)
                webhook_info = f"  webhook: {webhook}" if webhook else "  (sin webhook detectado)"
                _print_ok(f"Descargado: {wf_name}{webhook_info}")
            except Exception as exc:
                _print_err(f"No se pudo descargar '{url_o_id}': {exc}")
                flujos_fallidos.append({"url": url_o_id, "etapa": "descarga", "error": str(exc)})

    # Mapear webhooks a flujos n8n (archivos locales)
    webhook_map: Dict[str, str] = {}
    for i, n8n_file in enumerate(args.n8n):
        webhook_map[n8n_file] = args.webhooks[i] if i < len(args.webhooks) else ""

    # ──────────────────────────────────────────────────────────────────────────
    # FASE 1 — Análisis estático de todos los nodos (sin pruebas dinámicas)
    # ──────────────────────────────────────────────────────────────────────────
    # Cada entrada: {nombre, tipo, analisis, gpt_result, webhook, wf_data,
    #                agent_id, json_path, ajustado}
    previews: List[Dict] = []
    total_nodos = len(args.eleven) + len(args.n8n) + len(n8n_from_api)
    _print_info(f"Analizando {total_nodos} nodo(s)... (fase estatica)")

    # ElevenLabs
    for agent_id in args.eleven:
        _print_info(f"Analizando ElevenLabs: {agent_id}")
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("evaluar_elevenlabs", Path(__file__).parent / "evaluar_elevenlabs.py")
            _mod  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
            elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")
            data     = _mod.ElevenLabsClient(elevenlabs_key).obtener_agente(agent_id)
            name     = data.get("name", agent_id)
            analisis = _mod.ElevenLabsAnalyzer(data).analizar()
            gpt_result: Dict = {}
            if openai_key:
                try:
                    gpt_result = _mod.analizar_con_gpt(analisis, name)
                    analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
                except Exception:
                    pass
            previews.append({
                "nombre": name, "tipo": "elevenlabs", "analisis": analisis,
                "gpt_result": gpt_result, "agent_id": agent_id,
                "webhook": "", "wf_data": None, "json_path": "", "ajustado": False,
            })
            _print_ok(f"Listo: {name}")
        except Exception as exc:
            _print_err(f"No se pudo analizar {agent_id}: {exc}")
            flujos_fallidos.append({"url": agent_id, "etapa": "analisis", "error": str(exc)})

    # n8n archivos locales
    for n8n_file in args.n8n:
        _print_info(f"Analizando n8n (archivo): {n8n_file}")
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("evaluar_n8n", Path(__file__).parent / "evaluar_n8n.py")
            _mod  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
            wf   = json.loads(Path(n8n_file).read_text(encoding="utf-8"))
            name = wf.get("name", Path(n8n_file).stem)
            analisis = _mod.N8nAnalyzer(wf).analizar()
            gpt_result = {}
            if openai_key:
                try:
                    gpt_result = _mod.analizar_con_gpt(analisis, name)
                    analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
                except Exception:
                    pass
            if openai_key:
                try:
                    analisis = _mod.validar_y_enriquecer_modelos(analisis, openai_key)
                except Exception:
                    pass
            previews.append({
                "nombre": name, "tipo": "n8n", "analisis": analisis,
                "gpt_result": gpt_result, "agent_id": "",
                "webhook": webhook_map.get(n8n_file, ""), "wf_data": wf,
                "json_path": n8n_file, "ajustado": False,
            })
            _print_ok(f"Listo: {name}")
        except Exception as exc:
            _print_err(f"No se pudo analizar {n8n_file}: {exc}")
            flujos_fallidos.append({"url": n8n_file, "etapa": "analisis", "error": str(exc)})

    # n8n descargados via API
    for wf_data, webhook_url in n8n_from_api:
        name = wf_data.get("name", "flujo-n8n")
        _print_info(f"Analizando n8n (API): {name}")
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("evaluar_n8n", Path(__file__).parent / "evaluar_n8n.py")
            _mod  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
            analisis = _mod.N8nAnalyzer(wf_data).analizar()
            gpt_result = {}
            if openai_key:
                try:
                    gpt_result = _mod.analizar_con_gpt(analisis, name)
                    analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
                except Exception:
                    pass
            if openai_key:
                try:
                    analisis = _mod.validar_y_enriquecer_modelos(analisis, openai_key)
                except Exception:
                    pass
            previews.append({
                "nombre": name, "tipo": "n8n", "analisis": analisis,
                "gpt_result": gpt_result, "agent_id": "",
                "webhook": webhook_url, "wf_data": wf_data,
                "json_path": "", "ajustado": False,
            })
            _print_ok(f"Listo: {name}")
        except Exception as exc:
            _print_err(f"No se pudo analizar {name}: {exc}")
            flujos_fallidos.append({"url": name, "etapa": "analisis", "error": str(exc)})

    if not previews:
        _print_err("No se pudo analizar ningun nodo. Abortando.")
        sys.exit(1)

    # ──────────────────────────────────────────────────────────────────────────
    # MENU DE REVISION — el usuario ajusta los nodos que quiera, luego confirma
    # ──────────────────────────────────────────────────────────────────────────
    previews = _menu_revision_pipeline(previews, openai_key)

    # ──────────────────────────────────────────────────────────────────────────
    # FASE 2 — Evaluación dinámica con los análisis ya ajustados
    # ──────────────────────────────────────────────────────────────────────────
    node_results: List[NodeResult] = []
    _print_info("Iniciando pruebas dinamicas...")

    for pv in previews:
        nombre    = pv["nombre"]
        tipo      = pv["tipo"]
        analisis  = pv["analisis"]
        gpt_result = pv.get("gpt_result", {})
        _print_info(f"Probando: {nombre}")
        try:
            result = _spin(
                f"Probando {nombre}...",
                _evaluar_con_analisis,
                pv, openai_key, total_conv, args.concurrencia,
            )
            node_results.append(result)
            modo_label = "" if result.dynamic_tests_ran else "  (solo estatico — sin webhook activo)"
            _print_ok(f"{result.name}  ->  {result.scores.get('score_general', 0):.1f}%{modo_label}")
        except Exception as exc:
            _print_err(f"No se pudo evaluar {nombre}: {exc}")

    if not node_results:
        _print_err("No se pudo evaluar ningún nodo. Abortando.")
        sys.exit(1)

    # ── Construir grafo del pipeline ──────────────────────────────────────────
    _print_info("Detectando conexiones entre nodos...")
    graph = None
    try:
        from evaluation.pipeline.graph import build_pipeline_graph
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
        n_edges = len(graph.edges)
        n_gaps  = len(graph.gaps)
        _print_ok(f"Grafo construido: {n_edges} conexiones detectadas, {n_gaps} gaps")
        if graph.cycles:
            _print_err(f"Ciclos detectados: {graph.cycles}")
    except Exception as exc:
        _print_err(f"Grafo no disponible: {exc}")

    # ── Análisis de coherencia del pipeline ───────────────────────────────────
    _print_info("Analizando coherencia del pipeline...")
    analisis_coherencia: Dict = {}
    try:
        from evaluation.pipeline.analyzer import analizar_coherencia_pipeline
        nodes_summary = [
            {"name": r.name, "node_type": r.node_type, "analisis": r.analisis, "scores": r.scores}
            for r in node_results
        ]
        edges_summary = [
            {"source": e.source_id, "target": e.target_id, "match_type": e.match_type}
            for e in (graph.edges if graph else [])
        ]
        order = graph.order if graph else [r.node_id for r in node_results]
        # Fix: incluir nombre real del nodo en 'caller' (antes salía "nodo desconocido")
        gaps = []
        for _g in (graph.gaps if graph else []):
            _src_node = graph.nodes.get(_g.node_id) if graph else None
            _caller_name = _src_node.name if _src_node else _g.node_id
            gaps.append({
                "node_id":     _g.node_id,
                "exit_url":    _g.exit_url,
                "description": _g.description,
                "caller":      _caller_name,      # nombre legible del nodo fuente
                "endpoint":    _g.exit_url,       # URL sin receptor
            })

        analisis_coherencia = analizar_coherencia_pipeline(
            nodes_data=nodes_summary,
            edges=edges_summary,
            order=order,
            gaps=gaps,
            openai_key=openai_key,
        )
        _print_ok(f"Coherencia: {analisis_coherencia.get('score_coherencia', 0):.1f}%")
    except Exception as exc:
        _print_err(f"Análisis de coherencia no disponible: {exc}")
        analisis_coherencia = {"score_coherencia": 70.0, "riesgos": [], "recomendaciones": []}

    # ── Score final del pipeline ──────────────────────────────────────────────
    scores_pipeline = calcular_score_pipeline(node_results, analisis_coherencia)
    pipeline_name   = args.nombre or f"Pipeline ({', '.join(r.name for r in node_results[:2])}{'...' if len(node_results) > 2 else ''})"
    _print_ok(f"Score del pipeline: {scores_pipeline['score_general']:.1f}%")

    # ── Historial y benchmark ─────────────────────────────────────────────────
    anterior, comparacion, benchmark_sec = _persistir_pipeline(pipeline_name, scores_pipeline, node_results)

    # ── Reporte del pipeline ──────────────────────────────────────────────────
    reporte_pipeline = ""
    try:
        from evaluation.pipeline.reporter import generar_reporte_pipeline
        agent_results_for_report = [
            {"name": r.name, "node_type": r.node_type, "scores": r.scores, "reporte_texto": r.reporte_texto}
            for r in node_results
        ]
        # Inyectar scores del pipeline en el dict de análisis para el reporter
        analisis_coherencia_completo = {**analisis_coherencia, **scores_pipeline}
        reporte_pipeline = generar_reporte_pipeline(
            graph=graph,
            analisis_pipeline=analisis_coherencia_completo,
            agent_results=agent_results_for_report,
            pipeline_name=pipeline_name,
        )
    except Exception as exc:
        _print_err(f"Reporte del pipeline no disponible: {exc}")
        reporte_pipeline = f"\n[REPORTE DE PIPELINE — Error: {exc}]\n"

    # ── Guardar reporte ───────────────────────────────────────────────────────
    outputs = Path("outputs")
    outputs.mkdir(exist_ok=True)
    nombre_limpio = "".join(c for c in pipeline_name if c.isalnum() or c in " _-").strip().replace(" ", "_")[:50]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    salida = outputs / f"pipeline_{nombre_limpio}_{ts}.txt"

    # Sección de flujos que no se pudieron analizar (si los hay)
    flujos_fallidos_sec = ""
    if flujos_fallidos:
        SEP80 = "=" * 80
        lineas = [
            "",
            SEP80,
            "  FLUJOS NO ANALIZADOS",
            SEP80,
            "",
            f"  {len(flujos_fallidos)} flujo(s) no pudieron ser procesados:",
            "",
        ]
        for i, f in enumerate(flujos_fallidos, 1):
            lineas.append(f"  [{i}] {f['url']}")
            lineas.append(f"      Etapa : {f['etapa']}")
            lineas.append(f"      Error : {f['error'][:200]}")
            lineas.append("")
        flujos_fallidos_sec = "\n".join(lineas)

    partes = [comparacion, reporte_pipeline]
    if benchmark_sec:
        partes.append(benchmark_sec)
    if flujos_fallidos_sec:
        partes.append(flujos_fallidos_sec)
    salida.write_text("\n\n".join(filter(None, partes)), encoding="utf-8")

    if HAS_RICH:
        console.print(f"\n[bold green]Reporte guardado:[/bold green] {salida}")
    else:
        print(f"\nReporte guardado: {salida}")

    # ── Modo CI/CD ────────────────────────────────────────────────────────────
    if args.ci_mode:
        score_actual   = scores_pipeline["score_general"]
        score_anterior = (anterior or {}).get("score_general", None)
        threshold      = args.ci_threshold
        print("\n" + "=" * 60)
        print("  CI/CD MODE — PIPELINE")
        print("=" * 60)
        print(f"  Score actual   : {score_actual:.1f}%")
        if score_anterior is None:
            print("  Score anterior : (primera evaluacion)")
            print("  Resultado      : OK")
        else:
            diff = score_actual - score_anterior
            print(f"  Score anterior : {score_anterior:.1f}%")
            print(f"  Delta          : {diff:+.1f}pp  (umbral: -{threshold:.1f}pp)")
            if diff < -threshold:
                print(f"  Resultado      : FALLO — regresion de {abs(diff):.1f} puntos")
                print("=" * 60)
                sys.exit(1)
            else:
                print("  Resultado      : OK")
        print("=" * 60)


if __name__ == "__main__":
    main()
