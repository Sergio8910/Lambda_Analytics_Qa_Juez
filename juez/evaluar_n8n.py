#!/usr/bin/env python3
"""evaluar_n8n.py — Evaluador interactivo de flujos n8n para Lambda Analytics Juez.

Uso:
    python evaluar_n8n.py                              # interactivo (abre explorador)
    python evaluar_n8n.py ruta/flujo.json              # archivo local
    python evaluar_n8n.py https://tu-n8n.com/workflow/ID   # descarga via API de n8n

Con la URL del editor de n8n, el flujo se descarga automaticamente usando
N8N_BASE_URL / N8N_API_KEY del .env y el webhook se deriva del propio flujo,
asi el contra-agente puede ejecutarse sin pasar la URL del webhook a mano.
"""
from __future__ import annotations

# Permite correr "python juez/evaluar_n8n.py ..." sin -m, agregando el root
# del repo al sys.path antes de los imports de `juez.*`.
if __name__ == "__main__" and __package__ is None:
    import sys as _sys
    import pathlib as _pathlib
    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent.parent))

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dotenv import load_dotenv

load_dotenv()

# ─── Contra-agente (opcional) ─────────────────────────────────────────────────
try:
    from juez.evaluation.contra_agente.generator import generar_batch as _ca_generar_batch
    from juez.evaluation.contra_agente.pool import ejecutar_batch as _ca_ejecutar_batch
    from juez.evaluation.contra_agente.evaluator import TurnEvaluator as _TurnEvaluator
    from juez.evaluation.contra_agente.reporter import generar_reporte_batch as _ca_reporter
    from juez.evaluation.contra_agente.adapters.n8n import N8nAdapter as _N8nAdapter
    from juez.evaluation.contra_agente.verificador_client import healthcheck as _verificador_healthcheck
    HAS_CONTRA_AGENTE = True
except ImportError:
    HAS_CONTRA_AGENTE = False

# Forzar UTF-8 en stdout/stderr para terminales Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
os.environ.setdefault("DEEPEVAL_DISABLE_TELEMETRY", "1")

# ─── Rich (UI de terminal) ────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.prompt import Prompt
    from rich.table import Table

    console = Console(highlight=False, emoji=False)
    HAS_RICH = True
except ImportError:
    console = None
    HAS_RICH = False

# ─── OpenAI ──────────────────────────────────────────────────────────────────
try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


# =============================================================================
# CATÁLOGOS
# =============================================================================

TIPOS_STICKY: Set[str] = {"n8n-nodes-base.stickyNote"}

TIPOS_TRIGGER: Set[str] = {
    "n8n-nodes-base.webhook",
    "n8n-nodes-base.scheduleTrigger",
    "n8n-nodes-base.manualTrigger",
    "n8n-nodes-base.cron",
    "n8n-nodes-base.emailReadImap",
    "n8n-nodes-base.formTrigger",
    "@n8n/n8n-nodes-langchain.chatTrigger",
    "n8n-nodes-base.executeWorkflowTrigger",
    "n8n-nodes-base.errorTrigger",
}

NODE_LABELS: Dict[str, str] = {
    "@n8n/n8n-nodes-langchain.agent": "Agente IA",
    "@n8n/n8n-nodes-langchain.lmChatOpenAi": "Modelo LLM (OpenAI)",
    "@n8n/n8n-nodes-langchain.lmChatAnthropic": "Modelo LLM (Anthropic)",
    "@n8n/n8n-nodes-langchain.memoryPostgresChat": "Memoria Postgres",
    "@n8n/n8n-nodes-langchain.memoryBufferWindow": "Memoria Buffer",
    "@n8n/n8n-nodes-langchain.outputParserStructured": "Parser Output Estructurado",
    "@n8n/n8n-nodes-langchain.chainLlm": "Cadena LLM",
    "@n8n/n8n-nodes-langchain.toolWorkflow": "Tool: Sub-workflow",
    "@n8n/n8n-nodes-langchain.toolHttpRequest": "Tool: HTTP",
    "@n8n/n8n-nodes-langchain.toolPostgres": "Tool: Postgres",
    "@n8n/n8n-nodes-langchain.toolCode": "Tool: Código",
    "@n8n/n8n-nodes-langchain.vectorStoreRetriever": "Retriever Vectorial",
    "@n8n/n8n-nodes-langchain.vectorStorePinecone": "Vector Store (Pinecone)",
    "@n8n/n8n-nodes-langchain.vectorStoreSupabase": "Vector Store (Supabase)",
    "@n8n/n8n-nodes-langchain.vectorStoreQdrant": "Vector Store (Qdrant)",
    "@n8n/n8n-nodes-langchain.vectorStoreChroma": "Vector Store (Chroma)",
    "@n8n/n8n-nodes-langchain.embeddingsOpenAi": "Embeddings (OpenAI)",
    "@n8n/n8n-nodes-langchain.documentDefaultDataLoader": "Document Loader",
    "n8n-nodes-base.httpRequest": "Llamada HTTP",
    "n8n-nodes-base.httpRequestTool": "Tool: HTTP Request",
    "n8n-nodes-base.if": "Condicion IF",
    "n8n-nodes-base.switch": "Switch/Router",
    "n8n-nodes-base.set": "Asignar Variables",
    "n8n-nodes-base.code": "Codigo JavaScript",
    "n8n-nodes-base.respondToWebhook": "Respuesta Webhook",
    "n8n-nodes-base.webhook": "Webhook Entrada",
    "n8n-nodes-base.postgres": "Consulta Postgres",
    "n8n-nodes-base.merge": "Merge",
    "n8n-nodes-base.splitInBatches": "Split en Batches",
    "n8n-nodes-base.wait": "Espera",
    "n8n-nodes-base.noOp": "No-Op",
    "n8n-nodes-base.stickyNote": "Nota Adhesiva",
}

# Nodos satélite: se adjuntan a agentes/chains, no aparecen como cajas independientes
TIPOS_SATELITE: Set[str] = {
    "@n8n/n8n-nodes-langchain.lmChatOpenAi",
    "@n8n/n8n-nodes-langchain.lmChatAnthropic",
    "@n8n/n8n-nodes-langchain.memoryPostgresChat",
    "@n8n/n8n-nodes-langchain.memoryBufferWindow",
    "@n8n/n8n-nodes-langchain.outputParserStructured",
    "@n8n/n8n-nodes-langchain.vectorStoreRetriever",
    "@n8n/n8n-nodes-langchain.vectorStorePinecone",
    "@n8n/n8n-nodes-langchain.vectorStoreSupabase",
    "@n8n/n8n-nodes-langchain.vectorStoreQdrant",
    "@n8n/n8n-nodes-langchain.vectorStoreChroma",
    "@n8n/n8n-nodes-langchain.embeddingsOpenAi",
    "@n8n/n8n-nodes-langchain.documentDefaultDataLoader",
}

# Tools del agente (se listan separado de las APIs del flujo)
TIPOS_TOOL: Set[str] = {
    "@n8n/n8n-nodes-langchain.toolHttpRequest",
    "@n8n/n8n-nodes-langchain.toolWorkflow",
    "@n8n/n8n-nodes-langchain.toolPostgres",
    "@n8n/n8n-nodes-langchain.toolCode",
    "n8n-nodes-base.httpRequestTool",
}

TIPOS_EXCLUIR_FLUJO: Set[str] = TIPOS_SATELITE | TIPOS_STICKY | TIPOS_TOOL


# =============================================================================
# ANALIZADOR ESTÁTICO
# =============================================================================

class N8nAnalyzer:
    """Analiza estáticamente un flujo n8n sin necesidad de LLM."""

    def __init__(self, workflow: Dict[str, Any]) -> None:
        self.wf = workflow
        self.name: str = workflow.get("name", "Sin nombre")
        self.nodes: List[Dict] = workflow.get("nodes", [])
        self.connections: Dict = workflow.get("connections", {})
        self._by_name: Dict[str, Dict] = {n["name"]: n for n in self.nodes}
        self._by_id: Dict[str, Dict] = {n["id"]: n for n in self.nodes}
        # Grafo completo (calculado una vez)
        self._grafo_out, self._grafo_in = self._construir_grafo()

    # ── Grafo interno ─────────────────────────────────────────────────────────

    def _construir_grafo(self) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
        grafo_out: Dict[str, List[str]] = defaultdict(list)
        grafo_in: Dict[str, List[str]] = defaultdict(list)
        for origen, conn_map in self.connections.items():
            for _tipo, ramas in conn_map.items():
                for rama in ramas:
                    for dest_info in rama:
                        dest = dest_info.get("node", "")
                        if dest:
                            grafo_out[origen].append(dest)
                            grafo_in[dest].append(origen)
        return dict(grafo_out), dict(grafo_in)

    # ── Punto de entrada público ──────────────────────────────────────────────

    def analizar(self) -> Dict[str, Any]:
        return {
            "nombre": self.name,
            "trigger": self._tipo_trigger(),
            "inventario": self._inventario(),
            "flujo": self._flujo(),
            "apis": self._apis(),
            "nodos_ia": self._nodos_ia(),
            "herramientas": self._herramientas_agente(),
            "output_schemas": self._output_schemas(),
            "rag": self._rag(),
            "credenciales": self._credenciales(),
            "redundancias": self._redundancias(),
            "problemas": self._problemas(),
            "metricas": self._metricas(),
        }

    # ── Inventario ────────────────────────────────────────────────────────────

    def _inventario(self) -> List[Dict]:
        result = []
        for n in self.nodes:
            tipo = n.get("type", "unknown")
            cat = "principal"
            if tipo in TIPOS_SATELITE:
                cat = "satelite"
            elif tipo in TIPOS_TOOL:
                cat = "tool"
            elif tipo in TIPOS_STICKY:
                cat = "sticky"
            result.append({
                "nombre": n["name"],
                "tipo": tipo,
                "tipo_legible": NODE_LABELS.get(tipo, tipo),
                "id": n["id"],
                "version": n.get("typeVersion", "?"),
                "retry": n.get("retryOnFail", False),
                "siempre_output": n.get("alwaysOutputData", False),
                "notas": n.get("notes", ""),
                "categoria": cat,
            })
        return result

    # ── Análisis de flujo (corregido) ─────────────────────────────────────────

    def _flujo(self) -> Dict[str, Any]:
        grafo_out = self._grafo_out
        grafo_in = self._grafo_in

        # Solo nodos del flujo principal (excluir satélites, tools, sticky notes)
        nodos_flujo: Set[str] = {
            n["name"] for n in self.nodes
            if n.get("type") not in TIPOS_EXCLUIR_FLUJO
        }

        # Entradas: nodos del flujo sin predecesores en el flujo
        entradas = [
            n for n in nodos_flujo
            if not any(pred in nodos_flujo for pred in grafo_in.get(n, []))
        ]
        # Ordenar: triggers primero
        entradas_ord = (
            [n for n in entradas if self._by_name.get(n, {}).get("type") in TIPOS_TRIGGER]
            + [n for n in entradas if self._by_name.get(n, {}).get("type") not in TIPOS_TRIGGER]
        )

        # Salidas: nodos del flujo sin sucesores en el flujo
        salidas = [
            n for n in nodos_flujo
            if not any(dest in nodos_flujo for dest in grafo_out.get(n, []))
        ]

        # Aislados: sin ninguna conexión a otros nodos del flujo
        aislados = [
            n for n in nodos_flujo
            if not any(p in nodos_flujo for p in grafo_in.get(n, []))
            and not any(d in nodos_flujo for d in grafo_out.get(n, []))
            and n not in entradas
        ]

        condicionales = [
            n["name"] for n in self.nodes
            if n.get("type") in ("n8n-nodes-base.if", "n8n-nodes-base.switch")
        ]

        # BFS para calcular profundidad máxima desde todos los entry points
        profundidad_max = 0
        for entrada in entradas_ord:
            visitados: Set[str] = set()
            cola = [(entrada, 0)]
            while cola:
                nodo, p = cola.pop(0)
                if nodo in visitados:
                    continue
                visitados.add(nodo)
                profundidad_max = max(profundidad_max, p)
                for sig in grafo_out.get(nodo, []):
                    if sig in nodos_flujo and sig not in visitados:
                        cola.append((sig, p + 1))

        # Grafo solo con nodos del flujo
        grafo_flujo: Dict[str, List[str]] = {}
        for origen in nodos_flujo:
            dests = [d for d in grafo_out.get(origen, []) if d in nodos_flujo]
            if dests:
                grafo_flujo[origen] = dests

        return {
            "entradas": entradas_ord,
            "salidas": salidas,
            "aislados": aislados,
            "condicionales": condicionales,
            "profundidad": profundidad_max,
            "total_conexiones": sum(len(v) for v in grafo_out.values()),
            "grafo": grafo_flujo,
        }

    # ── Tipo de trigger y estrategia de evaluación ───────────────────────────

    def _tipo_trigger(self) -> Dict[str, Any]:
        """Detecta el tipo de trigger del flujo y genera la estrategia de evaluación dinámica."""
        _ESTRATEGIA: Dict[str, Dict] = {
            "n8n-nodes-base.webhook": {
                "tipo": "webhook",
                "label": "Webhook HTTP",
                "testeable": True,
                "estrategia": "POST directo al endpoint del webhook",
                "instrucciones": [],
            },
            "@n8n/n8n-nodes-langchain.chatTrigger": {
                "tipo": "chat_trigger",
                "label": "Chat Trigger (n8n built-in)",
                "testeable": True,
                "estrategia": "POST al webhook del chat trigger — misma estrategia que webhook normal",
                "instrucciones": [],
            },
            "n8n-nodes-base.emailReadImap": {
                "tipo": "email_imap",
                "label": "Email Trigger (IMAP)",
                "testeable": False,
                "estrategia": "Enviar un correo real a la cuenta monitoreada",
                "instrucciones": [
                    "Envia un correo de prueba a la cuenta IMAP configurada en el nodo",
                    "El asunto y cuerpo deben representar el caso de uso del agente",
                    "n8n comprobara la bandeja cada N minutos segun la configuracion",
                    "Revisa el historial de ejecuciones en n8n para confirmar el procesamiento",
                ],
            },
            "n8n-nodes-base.scheduleTrigger": {
                "tipo": "schedule",
                "label": "Schedule Trigger (cron)",
                "testeable": False,
                "estrategia": "Ejecutar manualmente desde el editor de n8n o esperar el siguiente ciclo",
                "instrucciones": [
                    "Abre el flujo en el editor de n8n",
                    "Haz clic en 'Test workflow' para ejecutarlo una vez manualmente",
                    "O espera al proximo ciclo de ejecucion programada",
                    "Revisa el historial de ejecuciones para ver los resultados",
                ],
            },
            "n8n-nodes-base.cron": {
                "tipo": "schedule",
                "label": "Cron Trigger",
                "testeable": False,
                "estrategia": "Ejecutar manualmente desde el editor de n8n",
                "instrucciones": [
                    "Abre el flujo en el editor de n8n",
                    "Haz clic en 'Test workflow' para ejecutarlo manualmente",
                ],
            },
            "n8n-nodes-base.manualTrigger": {
                "tipo": "manual",
                "label": "Manual Trigger",
                "testeable": False,
                "estrategia": "Ejecutar manualmente desde el editor de n8n",
                "instrucciones": [
                    "Abre el flujo en el editor de n8n",
                    "Haz clic en el boton 'Test workflow' o en el nodo Manual Trigger",
                ],
            },
            "n8n-nodes-base.executeWorkflowTrigger": {
                "tipo": "sub_workflow",
                "label": "Sub-Workflow (llamado por otro flujo)",
                "testeable": True,
                "estrategia": "Testear a traves del flujo padre que lo invoca via Execute Workflow",
                "instrucciones": [
                    "Este flujo es un sub-flujo invocado por otro flujo del pipeline",
                    "Para evaluarlo: corre el flujo padre y verifica que este sub-flujo se ejecute",
                    "El Juez puede detectar esta relacion si ambos flujos se pasan juntos al pipeline",
                ],
            },
            "n8n-nodes-base.formTrigger": {
                "tipo": "form",
                "label": "Form Trigger",
                "testeable": True,
                "estrategia": "POST al endpoint del formulario con los campos configurados",
                "instrucciones": [
                    "El formulario tiene una URL publica accesible",
                    "Enviar un POST con los campos del formulario al endpoint del form trigger",
                ],
            },
            "n8n-nodes-base.errorTrigger": {
                "tipo": "error",
                "label": "Error Trigger",
                "testeable": False,
                "estrategia": "Se activa automaticamente cuando otro flujo falla",
                "instrucciones": [
                    "Este flujo maneja errores de otros flujos",
                    "Para evaluarlo: provoca un error controlado en el flujo objetivo",
                ],
            },
        }

        trigger_nodes = []
        for n in self.nodes:
            tipo = n.get("type", "")
            if tipo in TIPOS_TRIGGER:
                trigger_nodes.append({
                    "nombre": n["name"],
                    "tipo_nodo": tipo,
                    "config": n.get("parameters", {}),
                })

        if not trigger_nodes:
            return {
                "tipo": "desconocido",
                "label": "Sin trigger detectado",
                "testeable": False,
                "nodos": [],
                "estrategia": "Revisar el flujo manualmente — no se encontro nodo de entrada",
                "instrucciones": ["No se detecto un nodo trigger. Verifica que el flujo este completo."],
            }

        # Usar el primer trigger como trigger principal
        trigger_principal = trigger_nodes[0]
        tipo_nodo = trigger_principal["tipo_nodo"]
        info = _ESTRATEGIA.get(tipo_nodo, {
            "tipo": tipo_nodo.split(".")[-1],
            "label": tipo_nodo,
            "testeable": False,
            "estrategia": "Tipo de trigger no catalogado — revisar manualmente",
            "instrucciones": ["Tipo de trigger desconocido para el Juez"],
        })

        return {
            **info,
            "nodos": trigger_nodes,
            "trigger_principal": trigger_principal["nombre"],
        }

    # ── APIs del flujo principal ───────────────────────────────────────────────

    def _apis(self) -> List[Dict]:
        apis = []
        for n in self.nodes:
            if n.get("type") != "n8n-nodes-base.httpRequest":
                continue
            p = n.get("parameters", {})
            headers = p.get("headerParameters", {}).get("parameters", [])
            apis.append({
                "nodo": n["name"],
                "metodo": p.get("method", "GET"),
                "url": p.get("url", ""),
                "con_auth": bool(p.get("sendHeaders")),
                "query_params": [h["name"] for h in p.get("queryParameters", {}).get("parameters", [])],
                "api_key_expuesta": self._api_key_hardcodeada(headers),
                "num_headers": len(headers),
            })
        return apis

    def _api_key_hardcodeada(self, headers: List[Dict]) -> bool:
        for h in headers:
            val = str(h.get("value", ""))
            name = h.get("name", "").lower()
            if (
                val
                and not val.startswith("={{")
                and len(val) > 8
                and any(k in name for k in ("key", "token", "auth", "secret", "api"))
            ):
                return True
        return False

    # ── Nodos IA (agentes y cadenas) ──────────────────────────────────────────

    def _nodos_ia(self) -> List[Dict]:
        tipos_ia = {
            "@n8n/n8n-nodes-langchain.agent",
            "@n8n/n8n-nodes-langchain.chainLlm",
        }
        resultado = []
        for n in self.nodes:
            if n.get("type") not in tipos_ia:
                continue
            p = n.get("parameters", {})
            sys_prompt = p.get("options", {}).get("systemMessage", "")
            user_prompt = p.get("text", "")

            # Detectar qué modelos y memoria tiene conectados
            modelos_conectados = [
                m["name"] for m in self.nodes
                if m.get("type") in {"@n8n/n8n-nodes-langchain.lmChatOpenAi",
                                      "@n8n/n8n-nodes-langchain.lmChatAnthropic"}
                and n["name"] in self._grafo_out.get(m["name"], [])
            ]
            memoria_conectada = [
                m["name"] for m in self.nodes
                if "memory" in m.get("type", "").lower()
                and n["name"] in self._grafo_out.get(m["name"], [])
            ]
            tools_conectadas = [
                t["name"] for t in self.nodes
                if t.get("type") in TIPOS_TOOL
                and n["name"] in self._grafo_out.get(t["name"], [])
            ]

            resultado.append({
                "nodo": n["name"],
                "tipo": NODE_LABELS.get(n["type"], n["type"]),
                "tiene_system_prompt": bool(sys_prompt),
                "chars_system_prompt": len(sys_prompt),
                "tiene_output_parser": bool(p.get("hasOutputParser")),
                "modelos_conectados": modelos_conectados,
                "memoria_conectada": memoria_conectada,
                "tools_conectadas": tools_conectadas,
                "system_prompt_preview": sys_prompt[:300],
                "system_prompt_completo": sys_prompt,
                "user_prompt_preview": user_prompt[:300],
            })
        return resultado

    # ── Herramientas del agente (NUEVO) ───────────────────────────────────────

    def _herramientas_agente(self) -> List[Dict]:
        # Mapear qué agente usa cada tool (a través del grafo)
        tool_a_agente: Dict[str, str] = {}
        agentes = {n["name"] for n in self.nodes if n.get("type") == "@n8n/n8n-nodes-langchain.agent"}
        for tool_n in self.nodes:
            if tool_n.get("type") not in TIPOS_TOOL:
                continue
            destinos = self._grafo_out.get(tool_n["name"], [])
            for dest in destinos:
                if dest in agentes:
                    tool_a_agente[tool_n["name"]] = dest

        tools = []
        for n in self.nodes:
            tipo = n.get("type", "")
            if tipo not in TIPOS_TOOL:
                continue
            p = n.get("parameters", {})

            # Descripcion que el agente ve
            descripcion = (
                p.get("toolDescription", "")
                or p.get("description", "")
                or p.get("name", "")
            )
            url = p.get("url", "")
            metodo = p.get("method", "GET")
            headers = p.get("headerParameters", {}).get("parameters", [])
            q_params = [h["name"] for h in p.get("queryParameters", {}).get("parameters", [])]
            body_params = list(p.get("bodyParameters", {}).get("parameters", []))

            tools.append({
                "nombre": n["name"],
                "tipo": NODE_LABELS.get(tipo, tipo),
                "agente_padre": tool_a_agente.get(n["name"], "(sin agente detectado)"),
                "descripcion": descripcion[:300] if descripcion else "(sin descripcion)",
                "url": url,
                "metodo": metodo,
                "con_auth": bool(p.get("sendHeaders")),
                "api_key_expuesta": self._api_key_hardcodeada(headers),
                "query_params": q_params,
                "body_params_count": len(body_params),
            })
        return tools

    # ── Structured Output Parser schemas (NUEVO) ──────────────────────────────

    def _output_schemas(self) -> List[Dict]:
        schemas = []
        for n in self.nodes:
            if n.get("type") != "@n8n/n8n-nodes-langchain.outputParserStructured":
                continue
            p = n.get("parameters", {})
            schema_str = p.get("inputSchema", "")
            autofix = bool(p.get("autoFix", False))

            if not schema_str:
                schemas.append({
                    "nodo": n["name"],
                    "tiene_schema": False,
                    "autofix": autofix,
                    "error": "Sin schema definido",
                })
                continue

            try:
                schema = json.loads(schema_str)
            except json.JSONDecodeError as exc:
                schemas.append({
                    "nodo": n["name"],
                    "tiene_schema": False,
                    "autofix": autofix,
                    "error": f"Schema JSON inválido: {exc}",
                })
                continue

            props = schema.get("properties", {})
            required_fields = schema.get("required", [])
            campos: List[Dict] = []

            for fname, fdef in props.items():
                tipo_campo = fdef.get("type", "")
                if "anyOf" in fdef:
                    tipos = [x.get("type", "?") for x in fdef["anyOf"]]
                    tipo_campo = f"anyOf({' | '.join(tipos)})"
                enums = fdef.get("enum", [])
                es_array = tipo_campo == "array"
                items_def = fdef.get("items", {})
                nested_props = items_def.get("properties", {}) if es_array else {}

                campos.append({
                    "nombre": fname,
                    "tipo": tipo_campo,
                    "requerido": fname in required_fields,
                    "tiene_descripcion": bool(fdef.get("description")),
                    "tiene_default": "default" in fdef,
                    "enum_values": enums,
                    "es_array": es_array,
                    "campos_anidados": list(nested_props.keys()) if nested_props else [],
                })

            # Detectar problemas del schema
            problemas_schema: List[str] = []
            sin_desc = [c["nombre"] for c in campos if not c["tiene_descripcion"]]
            if sin_desc:
                problemas_schema.append(f"Campos sin descripcion: {', '.join(sin_desc)}")

            requeridos_sin_default = [
                c["nombre"] for c in campos
                if c["requerido"] and not c["tiene_default"]
            ]
            if requeridos_sin_default:
                problemas_schema.append(
                    f"Campos requeridos sin valor por defecto: {', '.join(requeridos_sin_default)}"
                )

            enums_vacios = [c["nombre"] for c in campos if "enum" in props.get(c["nombre"], {}) and not c["enum_values"]]
            if enums_vacios:
                problemas_schema.append(f"Campos enum sin valores definidos: {', '.join(enums_vacios)}")

            if len(campos) > 20:
                problemas_schema.append(
                    f"Schema muy grande ({len(campos)} campos) — mayor riesgo de errores de parseo del LLM"
                )

            schemas.append({
                "nodo": n["name"],
                "tiene_schema": True,
                "autofix": autofix,
                "total_campos": len(campos),
                "campos_requeridos": [c["nombre"] for c in campos if c["requerido"]],
                "campos_opcionales": [c["nombre"] for c in campos if not c["requerido"]],
                "campos_enum": [(c["nombre"], c["enum_values"]) for c in campos if c["enum_values"]],
                "campos_array": [c["nombre"] for c in campos if c["es_array"]],
                "campos_anidados": [(c["nombre"], c["campos_anidados"]) for c in campos if c["campos_anidados"]],
                "problemas": problemas_schema,
                "campos_detalle": campos,
            })

        return schemas

    # ── Análisis RAG (NUEVO) ──────────────────────────────────────────────────

    def _rag(self) -> Dict[str, Any]:
        # RAG con vector store real
        tipos_vector: Set[str] = {
            "@n8n/n8n-nodes-langchain.vectorStoreRetriever",
            "@n8n/n8n-nodes-langchain.vectorStorePinecone",
            "@n8n/n8n-nodes-langchain.vectorStoreSupabase",
            "@n8n/n8n-nodes-langchain.vectorStoreQdrant",
            "@n8n/n8n-nodes-langchain.vectorStoreChroma",
            "@n8n/n8n-nodes-langchain.vectorStoreInMemory",
        }
        nodos_vector = [n for n in self.nodes if n.get("type") in tipos_vector]
        nodos_embed = [n for n in self.nodes if "embedding" in n.get("type", "").lower()]

        # RAG via HTTP (tools de búsqueda/catálogo)
        herramientas = self._herramientas_agente()
        keywords_retrieval = ("buscar", "search", "catalog", "catálog", "retriev", "consult", "query", "producto")
        http_retrieval = [
            h for h in herramientas
            if any(
                kw in h["nombre"].lower() or kw in h["descripcion"].lower()
                for kw in keywords_retrieval
            )
        ]

        if nodos_vector:
            tipo_rag = "vector_store"
        elif http_retrieval:
            tipo_rag = "http_retrieval"
        else:
            tipo_rag = "ninguno"

        problemas_rag: List[str] = []

        if tipo_rag == "vector_store":
            if not nodos_embed:
                problemas_rag.append("Vector store sin nodo de embeddings detectado — puede no funcionar")
            nodos_doc_loader = [n for n in self.nodes if "document" in n.get("type", "").lower()]
            if not nodos_doc_loader:
                problemas_rag.append("Sin document loader detectado — ¿cómo se ingestaron los documentos?")

        if tipo_rag == "http_retrieval":
            for h in http_retrieval:
                if not h.get("url"):
                    problemas_rag.append(f"Tool de retrieval '{h['nombre']}' sin URL configurada")
                if h.get("api_key_expuesta"):
                    problemas_rag.append(f"Tool de retrieval '{h['nombre']}' con API key hardcodeada")
                if h["descripcion"] == "(sin descripcion)":
                    problemas_rag.append(
                        f"Tool '{h['nombre']}' sin descripcion — el agente no sabe cuándo usarla"
                    )

        return {
            "tipo": tipo_rag,
            "descripcion_tipo": {
                "vector_store": "RAG con base de datos vectorial",
                "http_retrieval": "Retrieval via API HTTP (catalogo / busqueda externa)",
                "ninguno": "Sin sistema de retrieval detectado en el flujo",
            }[tipo_rag],
            "nodos_vector": [n["name"] for n in nodos_vector],
            "nodos_embeddings": [n["name"] for n in nodos_embed],
            "tools_retrieval": [h["nombre"] for h in http_retrieval],
            "problemas": problemas_rag,
            "tiene_rag": tipo_rag != "ninguno",
        }

    # ── Credenciales ──────────────────────────────────────────────────────────

    def _credenciales(self) -> Dict[str, Any]:
        por_tipo: Dict[str, List[str]] = defaultdict(list)
        nombres_unicos: Set[str] = set()

        for n in self.nodes:
            for tipo_cred, info in n.get("credentials", {}).items():
                nombre = info.get("name", "?")
                por_tipo[tipo_cred].append(f"  {n['name']} -> {nombre}")
                nombres_unicos.add(nombre)

        return {
            "por_tipo": dict(por_tipo),
            "total_unicas": len(nombres_unicos),
            "nombres": sorted(nombres_unicos),
        }

    # ── Redundancias ──────────────────────────────────────────────────────────

    def _redundancias(self) -> List[Dict]:
        redundancias = []

        # Modelos LLM con mismo valor
        por_modelo: Dict[str, List[str]] = defaultdict(list)
        for n in self.nodes:
            if "lmChat" not in n.get("type", ""):
                continue
            _m = n.get("parameters", {}).get("model", {})
            val = _m.get("value", "?") if isinstance(_m, dict) else str(_m)
            por_modelo[val].append(n["name"])
        for modelo, nods in por_modelo.items():
            if len(nods) > 1:
                redundancias.append({
                    "tipo": "Modelo LLM instanciado multiples veces",
                    "detalle": f"Modelo '{modelo}' aparece {len(nods)} veces",
                    "nodos": nods,
                    "severidad": "MEDIO",
                })

        # HTTP Requests a la misma URL base
        por_url: Dict[str, List[str]] = defaultdict(list)
        for n in self.nodes:
            if n.get("type") not in ("n8n-nodes-base.httpRequest",):
                continue
            url = n.get("parameters", {}).get("url", "").split("?")[0].rstrip("/")
            if url:
                por_url[url].append(n["name"])
        for url, nods in por_url.items():
            if len(nods) > 1:
                redundancias.append({
                    "tipo": "Misma URL desde multiples nodos HTTP",
                    "detalle": f"'{url}' en {len(nods)} nodos",
                    "nodos": nods,
                    "severidad": "BAJO",
                })

        # Código JS idéntico
        por_codigo: Dict[str, List[str]] = defaultdict(list)
        for n in self.nodes:
            if n.get("type") != "n8n-nodes-base.code":
                continue
            codigo = n.get("parameters", {}).get("jsCode", "")
            if codigo:
                por_codigo[codigo[:120]].append(n["name"])
        for _, nods in por_codigo.items():
            if len(nods) > 1:
                redundancias.append({
                    "tipo": "Codigo JavaScript duplicado",
                    "detalle": f"Logica identica en {len(nods)} nodos",
                    "nodos": nods,
                    "severidad": "MEDIO",
                })

        # Nodos Set con mismas asignaciones
        por_vars: Dict[str, List[str]] = defaultdict(list)
        for n in self.nodes:
            if n.get("type") != "n8n-nodes-base.set":
                continue
            names = tuple(sorted(
                a.get("name", "") for a in
                n.get("parameters", {}).get("assignments", {}).get("assignments", [])
            ))
            if names:
                por_vars[str(names)].append(n["name"])
        for _, nods in por_vars.items():
            if len(nods) > 1:
                redundancias.append({
                    "tipo": "Nodos Set con asignaciones identicas",
                    "detalle": f"Mismas variables en {len(nods)} nodos",
                    "nodos": nods,
                    "severidad": "BAJO",
                })

        return redundancias

    # ── Problemas ─────────────────────────────────────────────────────────────

    def _problemas(self) -> List[Dict]:
        problemas: List[Dict] = []

        # API keys hardcodeadas en HTTP requests del flujo principal
        for n in self.nodes:
            if n.get("type") not in ("n8n-nodes-base.httpRequest",):
                continue
            headers = n.get("parameters", {}).get("headerParameters", {}).get("parameters", [])
            if self._api_key_hardcodeada(headers):
                problemas.append({
                    "tipo": "Seguridad",
                    "descripcion": f"API key hardcodeada en '{n['name']}' (usar variables de entorno)",
                    "nodo": n["name"],
                    "severidad": "ALTO",
                })

        # API keys hardcodeadas en tools
        for n in self.nodes:
            if n.get("type") not in TIPOS_TOOL:
                continue
            headers = n.get("parameters", {}).get("headerParameters", {}).get("parameters", [])
            if self._api_key_hardcodeada(headers):
                problemas.append({
                    "tipo": "Seguridad",
                    "descripcion": f"API key hardcodeada en tool '{n['name']}' (usar variables de entorno)",
                    "nodo": n["name"],
                    "severidad": "ALTO",
                })

        # Agentes sin retry
        for n in self.nodes:
            if n.get("type") == "@n8n/n8n-nodes-langchain.agent" and not n.get("retryOnFail"):
                problemas.append({
                    "tipo": "Resiliencia",
                    "descripcion": f"Agente '{n['name']}' sin retryOnFail — un fallo de red tumba el flujo",
                    "nodo": n["name"],
                    "severidad": "MEDIO",
                })

        # Credenciales de prueba/dev
        for n in self.nodes:
            for _, info in n.get("credentials", {}).items():
                nombre = info.get("name", "").lower()
                if any(t in nombre for t in ("prueba", "test", "dev", "sandbox", "maribel", "staging")):
                    problemas.append({
                        "tipo": "Configuracion",
                        "descripcion": f"'{n['name']}' usa credencial de prueba: '{info.get('name')}'",
                        "nodo": n["name"],
                        "severidad": "ALTO",
                    })

        # Webhook sin respuesta
        tiene_webhook = any(n.get("type") == "n8n-nodes-base.webhook" for n in self.nodes)
        tiene_respuesta = any(n.get("type") == "n8n-nodes-base.respondToWebhook" for n in self.nodes)
        if tiene_webhook and not tiene_respuesta:
            problemas.append({
                "tipo": "Flujo",
                "descripcion": "Webhook de entrada sin nodo 'Respond to Webhook' — el cliente no recibirá respuesta",
                "nodo": "global",
                "severidad": "ALTO",
            })

        # Código JS complejo sin comentarios
        for n in self.nodes:
            if n.get("type") != "n8n-nodes-base.code":
                continue
            codigo = n.get("parameters", {}).get("jsCode", "")
            if len(codigo) > 300 and "//" not in codigo and "/*" not in codigo:
                problemas.append({
                    "tipo": "Mantenibilidad",
                    "descripcion": f"Codigo '{n['name']}' ({len(codigo)} chars) sin comentarios",
                    "nodo": n["name"],
                    "severidad": "BAJO",
                })

        # Código JS sin try/catch
        for n in self.nodes:
            if n.get("type") != "n8n-nodes-base.code":
                continue
            codigo = n.get("parameters", {}).get("jsCode", "")
            if len(codigo) > 100 and "try" not in codigo:
                problemas.append({
                    "tipo": "Resiliencia",
                    "descripcion": f"Codigo '{n['name']}' sin bloque try/catch — errores no controlados",
                    "nodo": n["name"],
                    "severidad": "BAJO",
                })

        # Nodos aislados del flujo
        flujo = self._flujo()
        for nodo in flujo.get("aislados", []):
            tipo = self._by_name.get(nodo, {}).get("type", "")
            if tipo not in TIPOS_EXCLUIR_FLUJO:
                problemas.append({
                    "tipo": "Flujo",
                    "descripcion": f"'{nodo}' no tiene conexiones — codigo muerto o incompleto",
                    "nodo": nodo,
                    "severidad": "MEDIO",
                })

        # Tools sin descripcion (el agente no sabe cuándo usarlas)
        for n in self.nodes:
            if n.get("type") not in TIPOS_TOOL:
                continue
            p = n.get("parameters", {})
            desc = p.get("toolDescription", "") or p.get("description", "")
            if not desc:
                problemas.append({
                    "tipo": "Configuracion IA",
                    "descripcion": f"Tool '{n['name']}' sin descripcion — el agente no sabe cuando invocarla",
                    "nodo": n["name"],
                    "severidad": "MEDIO",
                })

        # Problemas de schema
        for schema in self._output_schemas():
            for prob in schema.get("problemas", []):
                problemas.append({
                    "tipo": "Schema Output",
                    "descripcion": f"[{schema['nodo']}] {prob}",
                    "nodo": schema["nodo"],
                    "severidad": "MEDIO",
                })

        # Problemas de RAG
        for prob in self._rag().get("problemas", []):
            problemas.append({
                "tipo": "RAG / Retrieval",
                "descripcion": prob,
                "nodo": "global",
                "severidad": "MEDIO",
            })

        # Problemas de alineación tool ↔ prompt
        problemas.extend(self._alineacion_tools_prompt())

        return problemas

    # ── Alineación tools ↔ prompt ─────────────────────────────────────────────

    def _alineacion_tools_prompt(self) -> List[Dict[str, Any]]:
        """Detecta desalineaciones tool↔prompt para cada agente del flow.

        Delegado al módulo compartido `evaluation.static_checks.alignment`
        para que la misma lógica se aplique también a ElevenLabs y otros
        evaluadores futuros. Aquí solo normalizamos el input (n8n tiene N
        agentes, cada uno con su prompt y su subset de tools conectadas).
        """
        from juez.evaluation.static_checks import check_tool_prompt_alignment

        nodos_ia = self._nodos_ia()
        herramientas = self._herramientas_agente()
        if not nodos_ia or not herramientas:
            return []

        # Mapa agente -> nombres de tools conectadas a él
        tools_por_agente: Dict[str, List[str]] = defaultdict(list)
        for h in herramientas:
            tools_por_agente[h.get("agente_padre", "")].append(h["nombre"])

        problemas: List[Dict[str, Any]] = []
        for agente in nodos_ia:
            problemas.extend(check_tool_prompt_alignment(
                agent_name=agente.get("nodo", ""),
                system_prompt=agente.get("system_prompt_completo", "") or "",
                tool_names=tools_por_agente.get(agente.get("nodo", ""), []),
            ))
        return problemas

    # ── Métricas ──────────────────────────────────────────────────────────────

    def _metricas(self) -> Dict[str, Any]:
        tipos = Counter(n.get("type", "?") for n in self.nodes)
        por_tipo_legible = {NODE_LABELS.get(k, k): v for k, v in tipos.most_common()}

        total_prompt_chars = sum(
            len(n.get("parameters", {}).get("options", {}).get("systemMessage", ""))
            + len(n.get("parameters", {}).get("text", ""))
            for n in self.nodes
        )
        def _model_val(n):
            _m = n.get("parameters", {}).get("model", {})
            return _m.get("value", "?") if isinstance(_m, dict) else str(_m)
        modelos = sorted({
            _model_val(n)
            for n in self.nodes if "lmChat" in n.get("type", "")
        })
        # Mapa: valor_modelo -> [nombres de nodos IA que lo usan]
        modelos_por_nodo: Dict[str, List[str]] = {}
        for _mn in self.nodes:
            if "lmChat" not in _mn.get("type", ""):
                continue
            _mv = _mn.get("parameters", {}).get("model", {})
            _model_val_str = _mv.get("value", "?") if isinstance(_mv, dict) else str(_mv)
            for _dest in self._grafo_out.get(_mn["name"], []):
                _dest_node = self._by_name.get(_dest, {})
                if _dest_node.get("type") in {
                    "@n8n/n8n-nodes-langchain.agent",
                    "@n8n/n8n-nodes-langchain.chainLlm",
                }:
                    modelos_por_nodo.setdefault(_model_val_str, []).append(_dest)
        satelites = sum(1 for n in self.nodes if n.get("type") in TIPOS_SATELITE)
        tools = sum(1 for n in self.nodes if n.get("type") in TIPOS_TOOL)
        sticky = sum(1 for n in self.nodes if n.get("type") in TIPOS_STICKY)
        funcionales = len(self.nodes) - satelites - tools - sticky

        return {
            "total_nodos_json": len(self.nodes),
            "nodos_funcionales": funcionales,
            "nodos_satelite": satelites,
            "nodos_tools": tools,
            "sticky_notes": sticky,
            "total_conexiones_origen": len(self.connections),
            "nodos_ia": sum(1 for n in self.nodes if "langchain" in n.get("type", "")),
            "apis_externas": sum(1 for n in self.nodes if n.get("type") == "n8n-nodes-base.httpRequest"),
            "tools_agente": tools,
            "condicionales": sum(
                1 for n in self.nodes
                if n.get("type") in ("n8n-nodes-base.if", "n8n-nodes-base.switch")
            ),
            "nodos_codigo": sum(1 for n in self.nodes if n.get("type") == "n8n-nodes-base.code"),
            "chars_prompts": total_prompt_chars,
            "modelos_llm": modelos,
            "modelos_por_nodo": modelos_por_nodo,
            "distribucion_tipos": por_tipo_legible,
        }


# =============================================================================
# ANÁLISIS CON GPT
# =============================================================================

def analizar_con_gpt(analisis: Dict[str, Any], workflow_name: str) -> Dict[str, str]:
    if not HAS_OPENAI:
        return {"omitido": "Libreria openai no instalada"}
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"omitido": "OPENAI_API_KEY no configurada en .env"}

    client = OpenAI(api_key=api_key)
    modelo = os.getenv("JUDGE_MODEL", "gpt-4o")
    resultados: Dict[str, str] = {}

    # ── Análisis de prompts ──────────────────────────────────────────────────
    ia_nodos = analisis.get("nodos_ia", [])
    prompts_con_contenido = [n for n in ia_nodos if n.get("system_prompt_completo")]

    if prompts_con_contenido:
        texto_prompts = ""
        for n in prompts_con_contenido:
            texto_prompts += f"\n\n=== NODO: {n['nodo']} ({n['tipo']}) ===\n"
            texto_prompts += f"Longitud: {n['chars_system_prompt']} chars | "
            texto_prompts += f"Tools conectadas: {', '.join(n['tools_conectadas']) or 'ninguna'} | "
            texto_prompts += f"Output parser: {'si' if n['tiene_output_parser'] else 'no'}\n\n"
            prompt_txt = n["system_prompt_completo"][:5000]
            if len(n["system_prompt_completo"]) > 5000:
                prompt_txt += "\n[...truncado...]"
            texto_prompts += prompt_txt

        try:
            resp = client.chat.completions.create(
                model=modelo,
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Eres un experto en ingenieria de prompts para sistemas conversacionales en produccion. "
                            "Analiza los prompts de este flujo n8n con criterio critico y practico. "
                            "Responde en español. Cita secciones exactas del prompt cuando detectes problemas."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Analiza los prompts del flujo n8n '{workflow_name}':\n{texto_prompts}\n\n"
                            "Proporciona un análisis estructurado:\n"
                            "1. CALIDAD GENERAL (claridad, completitud, estructura)\n"
                            "2. PROBLEMAS ESPECIFICOS: instrucciones contradictorias, ambiguedades, "
                            "casos edge no cubiertos, reglas que se anulan entre si\n"
                            "3. RIESGOS EN PRODUCCION: comportamientos inesperados, inyeccion de prompts, "
                            "fugas de instrucciones internas\n"
                            "4. COMPLEJIDAD: ¿Cuántas reglas tiene? ¿Cuales son las 3 mas fragiles?\n"
                            "5. TOP 5 MEJORAS: ordenadas por impacto, con la seccion exacta a modificar"
                        ),
                    },
                ],
                max_tokens=2500,
            )
            resultados["analisis_prompts"] = resp.choices[0].message.content or ""
        except Exception as exc:
            resultados["analisis_prompts"] = f"Error al analizar prompts: {exc}"

        # ── Extracción de reglas de negocio ──────────────────────────────────
        try:
            resp_reglas = client.chat.completions.create(
                model=modelo,
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Eres un experto en análisis de sistemas de IA conversacional. "
                            "Extrae las reglas de negocio de un system prompt de agente. "
                            "Responde ÚNICAMENTE con JSON válido, sin texto adicional."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Analiza este system prompt de un agente de IA:\n\n{texto_prompts[:6000]}\n\n"
                            "Extrae las reglas en este JSON exacto:\n"
                            "{\"enfoque\": \"párrafo de 2-3 oraciones describiendo para qué existe este agente, quiénes son los usuarios típicos y qué vienen a resolver — esto define la identidad del contra-agente\", "
                            "\"no_puede\": [\"acciones, temas o compromisos que el agente tiene PROHIBIDO hacer, una por ítem\"], "
                            "\"reglas_clave\": [\"reglas de negocio específicas relevantes para evaluación, una por ítem\"], "
                            "\"casos_limite_criticos\": [\"situaciones concretas que probarían los límites — ej: 'usuario exige reembolso inmediato', 'usuario habla en inglés', 'usuario pide hablar con el gerente'\"], "
                            "\"dominio\": \"descripción en una línea del propósito del agente\"}"
                        ),
                    },
                ],
                max_tokens=1500,
                response_format={"type": "json_object"},
            )
            import json as _json_reglas
            resultados["reglas_negocio"] = _json_reglas.loads(resp_reglas.choices[0].message.content or "{}")
        except Exception as exc:
            resultados["reglas_negocio"] = {"error": str(exc)}

    # ── Análisis de tools y schemas ──────────────────────────────────────────
    herramientas = analisis.get("herramientas", [])
    schemas = analisis.get("output_schemas", [])
    rag = analisis.get("rag", {})

    if herramientas or schemas:
        ctx_tools = ""
        if herramientas:
            ctx_tools += "\n=== TOOLS DEL AGENTE ===\n"
            for h in herramientas:
                ctx_tools += (
                    f"- {h['nombre']} ({h['tipo']}) | agente: {h['agente_padre']}\n"
                    f"  URL: {h['url']} [{h['metodo']}]\n"
                    f"  Descripcion: {h['descripcion'][:200]}\n"
                    f"  Auth: {'si' if h['con_auth'] else 'no'} | "
                    f"API key expuesta: {'SI' if h['api_key_expuesta'] else 'no'}\n"
                )
        if schemas:
            ctx_tools += "\n=== OUTPUT SCHEMAS ===\n"
            for s in schemas:
                if not s.get("tiene_schema"):
                    ctx_tools += f"- {s['nodo']}: {s.get('error', 'sin schema')}\n"
                    continue
                ctx_tools += (
                    f"- {s['nodo']}: {s['total_campos']} campos | "
                    f"requeridos: {', '.join(s['campos_requeridos'][:6])} | "
                    f"arrays: {', '.join(s['campos_array']) or 'ninguno'} | "
                    f"autofix: {'si' if s['autofix'] else 'no'}\n"
                    f"  Problemas detectados: {'; '.join(s['problemas']) or 'ninguno'}\n"
                )

        if rag["tiene_rag"]:
            ctx_tools += f"\n=== RAG ===\n"
            ctx_tools += f"Tipo: {rag['descripcion_tipo']}\n"
            ctx_tools += f"Nodos: {', '.join(rag['tools_retrieval'] + rag['nodos_vector'])}\n"
            ctx_tools += f"Problemas: {'; '.join(rag['problemas']) or 'ninguno'}\n"

        try:
            resp3 = client.chat.completions.create(
                model=modelo,
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Eres un experto en agentes IA, diseño de tools para LLMs y sistemas RAG. "
                            "Analiza con criterio practico. Responde en español."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Analiza las tools, schemas de output y RAG del flujo '{workflow_name}':\n{ctx_tools}\n\n"
                            "Proporciona:\n"
                            "1. EVALUACION DE TOOLS: ¿Estan bien descritas para el agente? "
                            "¿Hay tools redundantes o con problemas de configuracion?\n"
                            "2. EVALUACION DEL SCHEMA DE OUTPUT: ¿Es el schema demasiado complejo? "
                            "¿Hay campos confusos o mal definidos? ¿Los enums son completos?\n"
                            "3. EVALUACION DEL RETRIEVAL: ¿La estrategia de busqueda es adecuada? "
                            "¿Hay riesgos de resultados incorrectos o vacios?\n"
                            "4. TOP 3 MEJORAS CRITICAS en tools/schema/RAG"
                        ),
                    },
                ],
                max_tokens=2000,
            )
            resultados["analisis_tools_schema"] = resp3.choices[0].message.content or ""
        except Exception as exc:
            resultados["analisis_tools_schema"] = f"Error al analizar tools/schema: {exc}"

    # ── Análisis arquitectural ────────────────────────────────────────────────
    flujo = analisis.get("flujo", {})
    metricas = analisis.get("metricas", {})
    problemas = analisis.get("problemas", [])

    resumen = {
        "nombre_flujo": workflow_name,
        "metricas": {k: v for k, v in metricas.items() if k != "distribucion_tipos"},
        "entradas": flujo.get("entradas"),
        "salidas": flujo.get("salidas"),
        "aislados": flujo.get("aislados"),
        "profundidad": flujo.get("profundidad"),
        "condicionales": flujo.get("condicionales"),
        "grafo": flujo.get("grafo"),
        "apis": [{"nodo": a["nodo"], "url": a["url"], "metodo": a["metodo"]} for a in analisis.get("apis", [])],
        "problemas_criticos": [p for p in problemas if p["severidad"] in ("CRITICO", "ALTO")],
        "redundancias": analisis.get("redundancias", []),
    }

    try:
        resp2 = client.chat.completions.create(
            model=modelo,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un arquitecto de software experto en automatizaciones n8n e IA conversacional. "
                        "Analiza con profundidad tecnica. Responde en español."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Analiza la arquitectura del flujo n8n:\n\n"
                        f"{json.dumps(resumen, indent=2, ensure_ascii=False)}\n\n"
                        "Responde con:\n"
                        "1. EVALUACION ARQUITECTURAL: Robustez, escalabilidad, mantenibilidad (1-10 cada uno)\n"
                        "2. PUNTOS DE FALLA CRITICOS: que puede romperse en produccion y por que\n"
                        "3. ANALISIS DE INTEGRACIONES: riesgos de las APIs, autenticacion, timeouts\n"
                        "4. LOGICA DE NEGOCIO: condiciones faltantes, ramas sin cobertura, estados imposibles\n"
                        "5. PLAN DE MEJORA PRIORIZADO: P0/P1/P2 con justificacion\n"
                        "6. EVALUACION DE REDUNDANCIAS: ¿son un problema real o tienen justificacion tecnica?"
                    ),
                },
            ],
            max_tokens=2500,
        )
        resultados["analisis_arquitectural"] = resp2.choices[0].message.content or ""
    except Exception as exc:
        resultados["analisis_arquitectural"] = f"Error al analizar arquitectura: {exc}"

    return resultados


# =============================================================================
# GENERADOR DE REPORTE TXT
# =============================================================================

def generar_reporte(
    analisis: Dict[str, Any],
    gpt: Dict[str, str],
    workflow_name: str,
    archivo_origen: str,
) -> str:
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lineas: List[str] = []

    def sep(c: str = "=", n: int = 80) -> None:
        lineas.append(c * n)

    def titulo(t: str) -> None:
        sep()
        lineas.append(t.center(80))
        sep()

    def seccion(t: str) -> None:
        lineas.append("")
        lineas.append(f"--- {t.upper()} {'-' * max(1, 75 - len(t))}")
        lineas.append("")

    def L(texto: str = "", indent: int = 2) -> None:
        lineas.append(" " * indent + texto)

    def gpt_block(texto: str) -> None:
        for ln in texto.split("\n"):
            L(ln)

    # ─────────────────────────────────────────────────────────────────────────
    titulo("EVALUACION DE FLUJO N8N — LAMBDA ANALYTICS JUEZ")
    L(f"Flujo     : {workflow_name}")
    L(f"Archivo   : {archivo_origen}")
    L(f"Fecha     : {ahora}")
    L(f"Motor     : Analisis estatico + GPT (Lambda Analytics Juez)")
    sep()

    # ── 1. Resumen Ejecutivo ──────────────────────────────────────────────────
    seccion("1. Resumen Ejecutivo")
    m = analisis["metricas"]
    todos_problemas = analisis["problemas"]
    p_altos = [p for p in todos_problemas if p["severidad"] in ("CRITICO", "ALTO")]
    p_medios = [p for p in todos_problemas if p["severidad"] == "MEDIO"]
    p_bajos = [p for p in todos_problemas if p["severidad"] == "BAJO"]
    rag = analisis["rag"]

    L(f"Nodos funcionales     : {m['nodos_funcionales']}  (cajas visibles en el canvas)")
    L(f"  + Tools del agente  : {m['nodos_tools']}  (herramientas invocables por el LLM)")
    L(f"  + Satelites         : {m['nodos_satelite']}  (LLMs, memoria, parsers adjuntos)")
    L(f"  + Sticky Notes      : {m['sticky_notes']}  (anotaciones sin logica)")
    L(f"  = Total en JSON     : {m['total_nodos_json']}")
    lineas.append("")
    L(f"Nodos IA/LangChain    : {m['nodos_ia']}")
    L(f"APIs externas (flujo) : {m['apis_externas']}")
    L(f"Tools del agente      : {m['tools_agente']}")
    L(f"Nodos condicionales   : {m['condicionales']}")
    L(f"Nodos codigo JS       : {m['nodos_codigo']}")
    L(f"Chars en prompts      : {m['chars_prompts']:,}")
    L(f"Modelos LLM usados    : {', '.join(m['modelos_llm']) or 'ninguno'}")
    L(f"Tipo de RAG           : {rag['descripcion_tipo']}")
    lineas.append("")
    L(f"Problemas detectados  : {len(todos_problemas)}")
    L(f"  [ALTO]              : {len(p_altos)}")
    L(f"  [MEDIO]             : {len(p_medios)}")
    L(f"  [BAJO]              : {len(p_bajos)}")
    L(f"Redundancias          : {len(analisis['redundancias'])}")

    # ── 2. Inventario de Nodos ────────────────────────────────────────────────
    seccion("2. Inventario de Nodos")
    cats = {"principal": [], "tool": [], "satelite": [], "sticky": []}
    for n in analisis["inventario"]:
        cats.setdefault(n["categoria"], []).append(n)

    for cat, label in (
        ("principal", "NODOS PRINCIPALES"),
        ("tool", "TOOLS DEL AGENTE"),
        ("satelite", "SATELITES"),
        ("sticky", "STICKY NOTES"),
    ):
        nods = cats.get(cat, [])
        if not nods:
            continue
        L(f"[ {label} ]  ({len(nods)} nodos)")
        for n in nods:
            retry = "retry:SI" if n["retry"] else "retry:NO"
            L(f"  {n['tipo_legible']}: {n['nombre']}  |  v{n['version']}  |  {retry}", indent=4)
            if n["notas"]:
                L(f"    Nota: {n['notas']}", indent=6)
        lineas.append("")

    # ── 3. Analisis de Flujo ──────────────────────────────────────────────────
    seccion("3. Analisis de Flujo")
    flujo = analisis["flujo"]
    L(f"Profundidad maxima del flujo : {flujo['profundidad']} niveles")
    L(f"Total conexiones             : {flujo['total_conexiones']}")
    lineas.append("")
    L("Puntos de ENTRADA (triggers / nodos sin predecesores):")
    for e in flujo["entradas"] or ["(ninguno detectado)"]:
        tipo_e = analisis.get("_by_name_cache", {}).get(e, {}).get("type", "")
        L(f"  --> {e}", indent=4)
    lineas.append("")
    L("Puntos de SALIDA (nodos sin sucesores en el flujo):")
    for s in flujo["salidas"] or ["(ninguno detectado)"]:
        L(f"  <-- {s}", indent=4)
    if flujo["aislados"]:
        lineas.append("")
        L("!! NODOS AISLADOS (sin conexiones al flujo principal):")
        for a in flujo["aislados"]:
            L(f"  ! {a}", indent=4)
    lineas.append("")
    L("Nodos condicionales (decision/bifurcacion):")
    for c in flujo["condicionales"] or ["(ninguno)"]:
        L(f"  <> {c}", indent=4)
    lineas.append("")
    L("Grafo de conexiones del flujo principal:")
    for origen, dests in flujo["grafo"].items():
        L(f"  {origen}  -->  {', '.join(dests)}", indent=4)

    # ── 3b. Trigger y Estrategia de Evaluacion ────────────────────────────────
    seccion("3b. Trigger y Estrategia de Evaluacion Dinamica")
    trigger_info = analisis.get("trigger", {})
    if trigger_info:
        testeable = trigger_info.get("testeable", False)
        L(f"Tipo de trigger    : {trigger_info.get('label', 'Desconocido')}")
        L(f"Testeable dinamicamente: {'SI' if testeable else 'NO'}")
        L(f"Estrategia         : {trigger_info.get('estrategia', '')}")
        nodos_trigger = trigger_info.get("nodos", [])
        if nodos_trigger:
            lineas.append("")
            L("Nodos de entrada detectados:")
            for nt in nodos_trigger:
                L(f"  --> {nt['nombre']}  [{nt['tipo_nodo']}]", indent=4)
        instrucciones = trigger_info.get("instrucciones", [])
        if instrucciones and not testeable:
            lineas.append("")
            L("Para evaluar este flujo dinamicamente:")
            for instr in instrucciones:
                L(f"  {instr}", indent=4)
    else:
        L("No se pudo determinar el tipo de trigger.")

    # ── 4. APIs del Flujo Principal ───────────────────────────────────────────
    seccion("4. APIs del Flujo Principal")
    if analisis["apis"]:
        for api in analisis["apis"]:
            alerta = "  !! API KEY HARDCODEADA" if api["api_key_expuesta"] else ""
            L(f"[{api['metodo']}] {api['nodo']}{alerta}")
            L(f"  URL         : {api['url']}", indent=4)
            L(f"  Auth        : {'Si (' + str(api['num_headers']) + ' headers)' if api['con_auth'] else 'No'}", indent=4)
            if api["query_params"]:
                L(f"  Query params: {', '.join(api['query_params'])}", indent=4)
            lineas.append("")
    else:
        L("No se detectaron nodos HTTP Request en el flujo principal.")

    # ── 5. Herramientas del Agente (NUEVO) ────────────────────────────────────
    seccion("5. Herramientas del Agente (Tools)")
    herramientas = analisis["herramientas"]
    if herramientas:
        # Agrupar por agente padre
        por_agente: Dict[str, List[Dict]] = defaultdict(list)
        for h in herramientas:
            por_agente[h["agente_padre"]].append(h)

        for agente, tools in por_agente.items():
            L(f"Agente: {agente}  ({len(tools)} tools)")
            for t in tools:
                alerta = "  !! API KEY EXPUESTA" if t["api_key_expuesta"] else ""
                L(f"  [{t['tipo']}] {t['nombre']}{alerta}", indent=4)
                if t["url"]:
                    L(f"    URL: {t['url']} [{t['metodo']}]", indent=6)
                L(f"    Descripcion para el LLM: {t['descripcion'][:180]}", indent=6)
                if t["query_params"]:
                    L(f"    Params: {', '.join(t['query_params'])}", indent=6)
            lineas.append("")
    else:
        L("No se detectaron tools adjuntas a agentes IA.")

    # ── 5B. Alineación Tools ↔ Prompt ─────────────────────────────────────────
    seccion("5B. Alineacion Tools <-> Prompt (estatico)")
    align_issues = [p for p in analisis.get("problemas", []) if p.get("tipo") == "Alineacion Tools"]
    no_mencionadas = [
        p for p in align_issues
        if "no se menciona en el system prompt" in p.get("descripcion", "")
    ]
    fantasma = [
        p for p in align_issues
        if "posible referencia rota" in p.get("descripcion", "")
    ]
    total_tools = len(analisis.get("herramientas", []))
    tools_mencionadas = total_tools - len(no_mencionadas)
    L(f"Tools del agente            : {total_tools}")
    L(f"Mencionadas en el prompt    : {tools_mencionadas} / {total_tools}")
    L(f"Sin mencion en el prompt    : {len(no_mencionadas)}")
    L(f"Referencias fantasma        : {len(fantasma)}  (nombres en el prompt que no son tools reales)")
    if no_mencionadas:
        lineas.append("")
        L("Tools sin mencion explicita en el prompt:")
        for p in no_mencionadas:
            L(f"  - {p['nodo']}", indent=4)
    if fantasma:
        lineas.append("")
        L("Referencias en el prompt que no corresponden a ninguna tool real:")
        # Extrae el identificador del mensaje (entre comillas) para mostrarlo limpio.
        # Nota: usar `match_ident` y no `m` — `m` es el dict de métricas en esta
        # función y reasignarlo rompía la sección 14 más abajo.
        for p in fantasma:
            match_ident = re.search(r"menciona '([^']+)'", p.get("descripcion", ""))
            ident = match_ident.group(1) if match_ident else "?"
            L(f"  - '{ident}'  (en agente: {p['nodo']})", indent=4)
    lineas.append("")

    # ── 6. Output Schemas (NUEVO) ─────────────────────────────────────────────
    seccion("6. Output Schemas (Structured Output Parser)")
    schemas = analisis["output_schemas"]
    if schemas:
        for s in schemas:
            L(f"Parser: {s['nodo']}  |  AutoFix: {'Si' if s.get('autofix') else 'No'}")
            if not s.get("tiene_schema"):
                L(f"  ERROR: {s.get('error', 'desconocido')}", indent=4)
                continue
            L(f"  Total campos    : {s['total_campos']}", indent=4)
            L(f"  Requeridos      : {', '.join(s['campos_requeridos'])}", indent=4)
            L(f"  Opcionales      : {', '.join(s['campos_opcionales'][:10])}{'...' if len(s['campos_opcionales']) > 10 else ''}", indent=4)
            if s["campos_enum"]:
                L(f"  Campos enum:", indent=4)
                for fname, vals in s["campos_enum"]:
                    L(f"    {fname}: [{', '.join(str(v) for v in vals)}]", indent=6)
            if s["campos_array"]:
                L(f"  Campos array    : {', '.join(s['campos_array'])}", indent=4)
            if s["campos_anidados"]:
                L(f"  Objetos anidados:", indent=4)
                for fname, sub_fields in s["campos_anidados"]:
                    L(f"    {fname} -> {', '.join(sub_fields)}", indent=6)
            if s["problemas"]:
                L(f"  Problemas:", indent=4)
                for p in s["problemas"]:
                    L(f"    !! {p}", indent=6)
            lineas.append("")
    else:
        L("No se detectaron nodos Structured Output Parser.")

    # ── 7. Analisis RAG / Retrieval (NUEVO) ───────────────────────────────────
    seccion("7. Analisis RAG y Retrieval")
    rag = analisis["rag"]
    L(f"Tipo de retrieval : {rag['descripcion_tipo']}")
    L(f"Tiene RAG         : {'Si' if rag['tiene_rag'] else 'No'}")
    if rag["nodos_vector"]:
        L(f"Vector stores     : {', '.join(rag['nodos_vector'])}")
    if rag["nodos_embeddings"]:
        L(f"Embeddings        : {', '.join(rag['nodos_embeddings'])}")
    if rag["tools_retrieval"]:
        L(f"Tools de busqueda : {', '.join(rag['tools_retrieval'])}")
    if rag["problemas"]:
        lineas.append("")
        L("Problemas detectados en retrieval:")
        for p in rag["problemas"]:
            L(f"  !! {p}", indent=4)
    else:
        L("Sin problemas detectados en el sistema de retrieval.")

    # ── 8. Auditoria de Credenciales ──────────────────────────────────────────
    seccion("8. Auditoria de Credenciales")
    creds = analisis["credenciales"]
    L(f"Total credenciales unicas : {creds['total_unicas']}")
    L(f"Nombres: {', '.join(creds['nombres'])}")
    lineas.append("")
    for tipo, usos in creds["por_tipo"].items():
        L(f"[{tipo}]")
        for u in usos:
            lineas.append(u)
    lineas.append("")

    # ── 9. Redundancias ───────────────────────────────────────────────────────
    seccion("9. Redundancias Detectadas")
    if analisis["redundancias"]:
        for r in analisis["redundancias"]:
            m_label = {"ALTO": "[ALTO]", "MEDIO": "[MEDIO]", "BAJO": "[BAJO]"}.get(r["severidad"], "[?]")
            L(f"{m_label} {r['tipo']}")
            L(f"  {r['detalle']}", indent=4)
            L(f"  Nodos: {', '.join(r['nodos'])}", indent=4)
            lineas.append("")
    else:
        L("No se detectaron redundancias significativas.")

    # ── 10. Problemas y Riesgos ───────────────────────────────────────────────
    seccion("10. Problemas y Riesgos Detectados")
    orden_sev = ["CRITICO", "ALTO", "MEDIO", "BAJO", "INFO"]
    todos_ord = sorted(
        todos_problemas,
        key=lambda x: orden_sev.index(x.get("severidad", "INFO")) if x.get("severidad", "INFO") in orden_sev else 99,
    )
    if todos_ord:
        for p in todos_ord:
            mk = {
                "CRITICO": "[!! CRITICO]", "ALTO": "[!  ALTO   ]",
                "MEDIO": "[-  MEDIO  ]", "BAJO": "[.  BAJO   ]",
            }.get(p["severidad"], "[INFO]")
            L(f"{mk} {p['tipo']}")
            L(f"  {p['descripcion']}", indent=4)
            L(f"  Nodo: {p['nodo']}", indent=4)
            lineas.append("")
    else:
        L("No se detectaron problemas en el analisis estatico.")

    # ── 11. Analisis Arquitectural (GPT) ──────────────────────────────────────
    seccion("11. Analisis Arquitectural — GPT")
    gpt_block(gpt.get("analisis_arquitectural") or gpt.get("omitido", "No disponible"))

    # ── 12. Evaluacion de Prompts (GPT) ───────────────────────────────────────
    seccion("12. Evaluacion de Prompts — GPT")
    gpt_block(gpt.get("analisis_prompts") or "No se encontraron prompts con contenido para analizar.")

    # ── 13. Evaluacion de Tools y Schema (GPT) ────────────────────────────────
    seccion("13. Evaluacion de Tools, Schema y RAG — GPT")
    gpt_block(gpt.get("analisis_tools_schema") or "No se enviaron tools ni schemas al analisis GPT.")

    # ── 14. Metricas Tecnicas ─────────────────────────────────────────────────
    seccion("14. Metricas Tecnicas")
    L(f"Total en JSON         : {m['total_nodos_json']}")
    L(f"  Funcionales         : {m['nodos_funcionales']}")
    L(f"  Tools agente        : {m['nodos_tools']}")
    L(f"  Satelites           : {m['nodos_satelite']}")
    L(f"  Sticky Notes        : {m['sticky_notes']}")
    L(f"Conexiones origen     : {m['total_conexiones_origen']}")
    L(f"Nodos IA/LangChain    : {m['nodos_ia']}")
    L(f"APIs HTTP (flujo)     : {m['apis_externas']}")
    L(f"Tools del agente      : {m['tools_agente']}")
    L(f"Nodos condicionales   : {m['condicionales']}")
    L(f"Nodos JS              : {m['nodos_codigo']}")
    L(f"Chars en prompts      : {m['chars_prompts']:,}")
    lineas.append("")
    L("Distribucion de nodos por tipo:")
    for tipo, count in m["distribucion_tipos"].items():
        L(f"  {tipo}: {count}", indent=4)

    # ─ Pie ────────────────────────────────────────────────────────────────────
    lineas.append("")
    sep()
    L("Reporte generado por Lambda Analytics Juez — Sistema de Evaluacion de Flujos IA")
    L(ahora)
    sep()

    return "\n".join(lineas)


# =============================================================================
# HEALTH CHECK — VERIFICACIÓN DE CONECTIVIDAD REAL
# =============================================================================

def health_check_n8n(webhook_url: str, analisis: Dict[str, Any], timeout: float = 8.0) -> Dict[str, Any]:
    """Verifica conectividad real del flujo n8n: webhook de entrada, tools y APIs.

    Clasifica: HEALTHY | DEGRADED | DOWN | SKIPPED
    """
    import time as _time
    import requests as _req

    _DUMMY_MAP = {
        "ciudad": "Bogotá", "city": "Bogotá",
        "direccion": "Calle 123 # 45-67", "dirección": "Calle 123 # 45-67", "address": "Calle 123 # 45-67",
        "nombre": "Juan Pérez", "name": "Juan Pérez",
        "cedula": "1234567890", "cédula": "1234567890", "id": "1234567890",
        "telefono": "3001234567", "teléfono": "3001234567", "phone": "3001234567",
        "fecha": "2026-01-15", "date": "2026-01-15",
        "pedido": "PED-001", "order": "PED-001", "order_id": "PED-001",
        "email": "test@ejemplo.com",
        "mensaje": "test", "message": "test", "query": "test",
    }

    def _dummy(campo: str) -> str:
        c = campo.lower()
        for kw, v in _DUMMY_MAP.items():
            if kw in c:
                return v
        return "test"

    def _call(url: str, method: str, payload: Dict) -> Dict[str, Any]:
        t0 = _time.time()
        try:
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if method == "GET":
                resp = _req.get(url, params=payload, headers=headers, timeout=timeout)
            else:
                resp = _req.request(method, url, json=payload, headers=headers, timeout=timeout)
            ms = round((_time.time() - t0) * 1000)
            try:
                body = resp.json()
                body_is_json = True
            except Exception:
                body = None
                body_is_json = False
            body_preview = str(body)[:300] if body is not None else resp.text[:300]
            if resp.ok:
                status = "HEALTHY" if body_is_json else "DEGRADED"
            else:
                status = "DOWN"
            return {
                "status": status, "http": resp.status_code, "ms": ms,
                "payload_enviado": payload, "body_preview": body_preview, "error": None,
            }
        except Exception as exc:
            ms = round((_time.time() - t0) * 1000)
            return {"status": "DOWN", "http": None, "ms": ms,
                    "payload_enviado": payload, "body_preview": "", "error": str(exc)[:200]}

    resultados: Dict[str, Any] = {}

    # 1. Webhook de entrada del flujo
    if webhook_url:
        payload_entrada = {"message": "test", "sessionId": "health_check"}
        r = _call(webhook_url, "POST", payload_entrada)
        resultados["__entrada__"] = {**r, "url": webhook_url, "etiqueta": "Webhook de entrada (flujo principal)"}

    # 2. Tools del agente — payload inteligente basado en query_params
    for tool in analisis.get("herramientas", []):
        url = tool.get("url", "")
        nombre = tool.get("nombre", url)
        if not url:
            resultados[nombre] = {"url": "", "status": "SKIPPED",
                                  "motivo": "Sin URL configurada", "ms": 0, "etiqueta": nombre}
            continue
        if url.startswith("={{"):
            resultados[nombre] = {"url": url, "status": "SKIPPED",
                                  "motivo": "URL dinamica — no verificable estaticamente", "ms": 0, "etiqueta": nombre}
            continue
        campos = tool.get("query_params", [])
        payload = {c: _dummy(c) for c in campos} if campos else {"test": "1"}
        metodo = tool.get("metodo", "POST").upper()
        r = _call(url, metodo, payload)
        resultados[nombre] = {**r, "url": url, "etiqueta": nombre}

    # 3. APIs HTTP del flujo principal (no tools)
    for api in analisis.get("apis", []):
        url = api.get("url", "")
        nombre = f"api:{api.get('nodo', url)}"
        if not url or url.startswith("={{"):
            resultados[nombre] = {"url": url, "status": "SKIPPED",
                                  "motivo": "URL dinamica — no verificable estaticamente", "ms": 0, "etiqueta": nombre}
            continue
        params = {p: _dummy(p) for p in api.get("query_params", [])}
        metodo = api.get("metodo", "GET").upper()
        r = _call(url, metodo, params)
        resultados[nombre] = {**r, "url": url, "etiqueta": nombre}

    return resultados


def generar_reporte_health(health: Dict[str, Any], nombre_flujo: str) -> str:
    """Genera la sección de health check para incluir en el reporte de validación."""
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lineas: List[str] = []

    def sep(c: str = "=", n: int = 80) -> None:
        lineas.append(c * n)

    sep()
    lineas.append("VALIDACION DE CONECTIVIDAD — HEALTH CHECK".center(80))
    lineas.append(f"Flujo: {nombre_flujo}  |  {ahora}".center(80))
    sep()
    lineas.append("")

    if not health:
        lineas.append("  No se verificaron endpoints (sin URL de webhook proporcionada).")
        return "\n".join(lineas)

    healthy = [k for k, v in health.items() if v.get("status") == "HEALTHY"]
    down    = [k for k, v in health.items() if v.get("status") == "DOWN"]
    skipped = [k for k, v in health.items() if v.get("status") == "SKIPPED"]

    lineas.append(f"  HEALTHY : {len(healthy)}  |  DOWN : {len(down)}  |  SKIPPED : {len(skipped)}")
    lineas.append("")

    for nombre, r in health.items():
        etiqueta = r.get("etiqueta", nombre)
        status   = r.get("status", "?")
        url      = r.get("url", "")
        ms       = r.get("ms", 0)
        http     = r.get("http", "")
        motivo   = r.get("motivo", "")
        error    = r.get("error", "")
        payload  = r.get("payload_enviado", {})
        preview  = r.get("body_preview", "")

        marker = {"HEALTHY": "[OK  ]", "DEGRADED": "[DEG ]", "DOWN": "[!! ]", "SKIPPED": "[--  ]"}.get(status, "[?   ]")
        lineas.append(f"  {marker} {etiqueta}")
        if url:
            lineas.append(f"         URL     : {url}")
        if http:
            lineas.append(f"         HTTP    : {http}  ({ms} ms)")
        if payload:
            lineas.append(f"         Payload : {str(payload)[:120]}")
        if preview:
            lineas.append(f"         Respuesta: {preview[:150]}")
        if motivo:
            lineas.append(f"         Motivo  : {motivo}")
        if error:
            lineas.append(f"         Error   : {error}")
        lineas.append("")

    if down:
        sep("-", 80)
        lineas.append("  ENDPOINTS CAIDOS:")
        for k in down:
            r = health[k]
            lineas.append(f"    !! {k}")
            lineas.append(f"       {r.get('url', '')}  —  {r.get('motivo', '')}")
        lineas.append("")

    sep()
    return "\n".join(lineas)


# =============================================================================
# CONTRA-AGENTE — CONVERSIÓN Y EJECUCIÓN
# =============================================================================

def _convertir_analisis_para_contra_agente(analisis_n8n: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte el análisis de N8nAnalyzer al formato que espera generar_batch."""
    nodos_ia = analisis_n8n.get("nodos_ia", [])
    system_prompt = nodos_ia[0].get("system_prompt_completo", "") if nodos_ia else ""

    tools = []
    for h in analisis_n8n.get("herramientas", []):
        campos = h.get("query_params", [])
        tools.append({
            "nombre": h.get("nombre", ""),
            "tipo": "webhook",
            "descripcion": h.get("descripcion", ""),
            "campos_requeridos": campos,
            "url": h.get("url", ""),
        })

    nombre = analisis_n8n.get("nombre", "n8n_agent")
    return {
        "agent_id": nombre,
        "identidad": {"idioma": "es", "nombre_agente": nombre},
        "prompt": {"completo": system_prompt},
        "tools": tools,
        "herramientas": tools,
        "reglas_negocio": analisis_n8n.get("reglas_negocio", {}),
    }


def _pedir_webhook_url() -> str:
    """Pide al usuario la URL del webhook del flujo n8n en producción."""
    if HAS_RICH:
        console.print("\n[bold cyan]Contra-agente:[/bold cyan] Para evaluar conversaciones reales, "
                      "necesito la URL del webhook del flujo n8n.")
        console.print("[dim]Ejemplo: https://mi-n8n.com/webhook/abc123[/dim]")
        url = Prompt.ask("[cyan]  URL del webhook[/cyan] (Enter para omitir)")
    else:
        print("\nContra-agente: ingresa la URL del webhook del flujo n8n.")
        print("Ejemplo: https://mi-n8n.com/webhook/abc123")
        url = input("  URL del webhook (Enter para omitir): ").strip()
    return url.strip()


def ejecutar_contra_agente(
    analisis_n8n: Dict[str, Any],
    webhook_url: str,
    agent_name: str,
    total_conv: int = 10,
    concurrencia: int = 3,
    distribucion_override: Optional[Dict[str, int]] = None,
    escenarios_extra: Optional[List[str]] = None,
    e2e_cases: int = 0,
    e2e_model: str = "",
    e2e_real_inventario_id: Optional[int] = None,
) -> tuple:
    """Corre el contra-agente contra el flujo n8n. Retorna (batch_result, reporte_texto).

    Si `e2e_real_inventario_id` está set, los casos e2e usan datos reales de
    la BD productiva de Abad (read-only) para el snapshot esperado.
    """
    if not HAS_CONTRA_AGENTE:
        return None, "\n[CONTRA-AGENTE NO DISPONIBLE — módulos evaluation/ no encontrados]\n"

    openai_key = os.getenv("OPENAI_API_KEY", "")
    analisis_ca = _convertir_analisis_para_contra_agente(analisis_n8n)

    if HAS_RICH:
        console.print(f"\n[dim]Generando {total_conv} planes de conversación...[/dim]")

    batch = _ca_generar_batch(
        analisis=analisis_ca,
        agent_name=agent_name,
        total=total_conv,
        concurrency=concurrencia,
        adapter="n8n",
        openai_key=openai_key,
        escenarios_extra=escenarios_extra or [],
        distribucion_override=distribucion_override,
        e2e_k=e2e_cases,
        e2e_real_inventario_id=e2e_real_inventario_id,
    )

    def _adapter_factory(_adapter_type: str, _agent_id: str):
        return _N8nAdapter(webhook_url=webhook_url)

    evaluator = _TurnEvaluator(openai_key=openai_key)

    if HAS_RICH:
        with Progress(SpinnerColumn(), TextColumn("[cyan]Ejecutando conversaciones..."), console=console, transient=True) as p:
            p.add_task("", total=None)
            batch_result = _ca_ejecutar_batch(
                batch,
                _adapter_factory,
                evaluator,
                openai_key=openai_key,
                synthetic_context={
                    "system_prompt": analisis_ca.get("prompt", {}).get("completo", ""),
                    "herramientas": analisis_ca.get("herramientas", []),
                    "model": e2e_model or os.getenv("JUEZ_E2E_MODEL", "gpt-4o-mini"),
                },
            )
    else:
        print("  Ejecutando conversaciones...")
        batch_result = _ca_ejecutar_batch(
            batch,
            _adapter_factory,
            evaluator,
            openai_key=openai_key,
            synthetic_context={
                "system_prompt": analisis_ca.get("prompt", {}).get("completo", ""),
                "herramientas": analisis_ca.get("herramientas", []),
                "model": e2e_model or os.getenv("JUEZ_E2E_MODEL", "gpt-4o-mini"),
            },
        )

    return batch_result, _ca_reporter(batch_result, agent_name=agent_name)


# =============================================================================
# INTERFAZ DE TERMINAL
# =============================================================================

def _ask_yes_no(prompt: str, default_yes: bool = False) -> bool:
    """Pregunta sí/no estandarizada (compatible con Rich y plain)."""
    default_str = "Y/n" if default_yes else "y/N"
    if HAS_RICH:
        raw = Prompt.ask(f"  {prompt}", default="y" if default_yes else "n")
    else:
        raw = input(f"  {prompt} [{default_str}]: ").strip() or ("y" if default_yes else "n")
    return raw.lower().startswith("y") or raw.lower() in ("s", "si", "sí")


def _ask_int(prompt: str, default: int, minimum: int = 1) -> int:
    """Pregunta un entero con default y mínimo."""
    if HAS_RICH:
        raw = Prompt.ask(f"  {prompt}", default=str(default))
    else:
        raw = input(f"  {prompt} (default {default}): ").strip() or str(default)
    try:
        v = int(raw)
        return max(minimum, v)
    except ValueError:
        return default


def _preguntar_config_e2e(default_cases: int = 1) -> Dict[str, Any]:
    """Sub-menú para configurar el modo e2e. Retorna dict con e2e_cases y
    e2e_real_inventario_id (None si sintético)."""
    cases = _ask_int("¿Cuantos casos e2e quieres?", default=default_cases, minimum=1)
    usar_real = _ask_yes_no(
        "¿Usar datos REALES de la BD productiva (read-only) para el snapshot?",
        default_yes=False,
    )
    real_inv_id: Optional[int] = None
    if usar_real:
        # Sugerir inventarios disponibles si se puede consultar la BD
        try:
            from juez.evaluation.contra_agente.synthetic.real_db_source import (
                listar_inventarios_disponibles,
            )
            invs = listar_inventarios_disponibles()
            invs_con_fotos = [i for i in invs if i["fotos"] > 0]
            if invs_con_fotos:
                if HAS_RICH:
                    console.print(
                        "\n  [dim]Inventarios disponibles con fotos:[/dim]"
                    )
                else:
                    print("\n  Inventarios disponibles con fotos:")
                for i in invs_con_fotos:
                    print(
                        f"    id={i['inventario_id']:3d} contrato={i['contrato_id']:>4} "
                        f"ambientes={i['ambientes']:2d} fotos={i['fotos']:3d}"
                    )
                # Default: el que tenga más fotos
                mejor = max(invs_con_fotos, key=lambda x: x["fotos"])
                default_inv = mejor["inventario_id"]
            else:
                default_inv = 1
        except Exception as exc:
            if HAS_RICH:
                console.print(
                    f"\n  [yellow]No pude listar inventarios desde la BD ({type(exc).__name__}). "
                    "Pide el id manualmente.[/yellow]"
                )
            else:
                print(f"\n  No pude listar inventarios desde la BD ({type(exc).__name__}). "
                      "Pide el id manualmente.")
            default_inv = 1
        real_inv_id = _ask_int("¿Cual inventario_id usar?", default=default_inv, minimum=1)
    return {"e2e_cases": cases, "e2e_real_inventario_id": real_inv_id}


def _seleccionar_modo_analisis() -> Dict[str, Any]:
    """Pregunta al usuario qué tipo de análisis ejecutar.

    Retorna un dict con:
        modo: "completo" | "validacion"
        e2e: bool                         (¿activar auditoría de artefacto via Verificador?)
        e2e_cases: int                    (cuántos planes e2e — solo si e2e=True)
        e2e_real_inventario_id: Optional[int]  (None=sintético, int=BD real read-only)

    CLI flags que saltean preguntas:
      --validacion / --completo                → fijan el modo
      --e2e                                    → activa e2e (omite la pregunta)
      --e2e-cases K                            → fija cuántos
      --e2e-real-inventario-id N               → fija el inventario real
    """
    # Flags del CLI que saltean preguntas
    cli_e2e = "--e2e" in sys.argv
    cli_validacion = "--validacion" in sys.argv
    cli_completo = "--completo" in sys.argv

    # ── Determinar modo + si el menu fuerza e2e ─────────────────────────
    e2e_forzado_por_menu = False
    if cli_validacion:
        modo = "validacion"
    elif cli_completo:
        modo = "completo"
    else:
        if HAS_RICH:
            console.print("\n[bold cyan]" + "═" * 60 + "[/bold cyan]")
            console.print("[bold white]   MODO DE ANALISIS[/bold white]")
            console.print("[bold cyan]" + "═" * 60 + "[/bold cyan]\n")
            console.print("  [bold][1][/bold] Completo                       "
                          "— Estatico + GPT + conversaciones contra el agente")
            console.print("  [bold][2][/bold] Validacion                     "
                          "— Solo conectividad y estructura (rapido)")
            console.print("  [bold][3][/bold] Completo + Verificacion e2e    "
                          "— Completo + auditoria sintetica del artefacto via Verificador\n")
            raw = Prompt.ask("  Modo", choices=["1", "2", "3"], default="1")
        else:
            print("\n" + "=" * 60)
            print("  MODO DE ANALISIS")
            print("=" * 60)
            print("  [1] Completo                       — Estatico + GPT + conversaciones")
            print("  [2] Validacion                     — Solo conectividad y estructura")
            print("  [3] Completo + Verificacion e2e    — Completo + auditoria del artefacto")
            raw = input("\n  Modo [1/2/3] (default 1): ").strip() or "1"

        if raw == "2":
            modo = "validacion"
        elif raw == "3":
            modo = "completo"
            e2e_forzado_por_menu = True
        else:
            modo = "completo"

    # ── Configurar parámetros de e2e ─────────────────────────────────────
    # Casos:
    #   a) CLI pasó --e2e         → no preguntamos, main usa args.e2e_*
    #   b) Menú eligió [3]        → preguntamos interactivamente cantidad/BD
    #   c) Menú eligió [1]        → preguntamos OPCIONAL si quiere e2e
    #   d) Modo validacion        → e2e no aplica
    e2e_cases = 0
    e2e_real_inventario_id: Optional[int] = None
    e2e_activado = False

    if modo == "validacion":
        pass
    elif cli_e2e:
        # El CLI ya pasó --e2e — main leerá args.e2e_cases / args.e2e_real_inventario_id
        e2e_activado = True
    elif e2e_forzado_por_menu:
        cfg = _preguntar_config_e2e(default_cases=1)
        e2e_cases = cfg["e2e_cases"]
        e2e_real_inventario_id = cfg["e2e_real_inventario_id"]
        e2e_activado = True
    else:
        # Opción [1]: preguntar opcional
        if _ask_yes_no(
            "¿Agregar caso(s) e2e con auditoria del Verificador?",
            default_yes=False,
        ):
            cfg = _preguntar_config_e2e(default_cases=1)
            e2e_cases = cfg["e2e_cases"]
            e2e_real_inventario_id = cfg["e2e_real_inventario_id"]
            e2e_activado = True

    return {
        "modo": modo,
        "e2e": e2e_activado,
        "e2e_cases": e2e_cases,
        "e2e_real_inventario_id": e2e_real_inventario_id,
    }


def banner() -> None:
    if HAS_RICH:
        console.print(
            Panel.fit(
                "[bold cyan]LAMBDA ANALYTICS[/bold cyan] [bold white]JUEZ[/bold white]\n"
                "[dim]Evaluador de Flujos n8n[/dim]\n"
                "[dim]Nodos · APIs · Tools · Schema · RAG · Flujo · Prompts[/dim]",
                border_style="cyan",
                padding=(1, 4),
            )
        )
    else:
        print("=" * 60)
        print("  LAMBDA ANALYTICS JUEZ — Evaluador de Flujos n8n")
        print("=" * 60)


def pedir_archivo() -> Optional[Path]:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        if HAS_RICH:
            console.print("\n[dim]Abriendo explorador de archivos...[/dim]")
        else:
            print("\nAbriendo explorador de archivos...")

        ruta_str = filedialog.askopenfilename(
            title="Seleccionar flujo n8n",
            filetypes=[("Archivos JSON", "*.json"), ("Todos los archivos", "*.*")],
        )
        root.destroy()

        if not ruta_str:
            _print_error("No se selecciono ningun archivo.")
            return None

        ruta = Path(ruta_str)
        if HAS_RICH:
            console.print(f"[dim]Archivo seleccionado: {ruta.name}[/dim]")
        return ruta

    except Exception as exc:
        if HAS_RICH:
            console.print(f"[yellow]Explorador no disponible ({exc}). Ingresa la ruta manualmente:[/yellow]")
            raw = Prompt.ask("[cyan]  Archivo[/cyan]")
        else:
            print("Explorador no disponible. Ingresa la ruta manualmente:")
            raw = input("  Archivo: ")

        ruta = Path(raw.strip().strip('"').strip("'"))
        if not ruta.exists():
            _print_error(f"Archivo no encontrado: {ruta}")
            return None
        return ruta


def _parsear_url_workflow(url_o_id: str) -> Tuple[str, str]:
    """Extrae (base_url, workflow_id) de una URL de n8n o de un ID puro.

    Acepta:
      - URL del editor : https://tu-n8n.com/workflow/WORKFLOW_ID
      - Solo el ID     : WORKFLOW_ID

    Retorna ("", id) si es un ID puro (base_url vendra del .env).
    """
    s = url_o_id.strip()
    if s.startswith("http://") or s.startswith("https://"):
        import re
        from urllib.parse import urlparse
        m = re.search(r"/workflow/([^/?#]+)", s)
        if m:
            p = urlparse(s)
            return f"{p.scheme}://{p.netloc}", m.group(1)
        raise ValueError(
            f"No se pudo extraer el ID del flujo de la URL: {s}\n"
            "  Formato esperado: https://tu-n8n.com/workflow/WORKFLOW_ID"
        )
    return "", s


def _descargar_workflow_n8n(base_url: str, api_key: str, workflow_id: str) -> Dict[str, Any]:
    """Descarga el JSON de un flujo n8n via GET {base_url}/api/v1/workflows/{id}."""
    try:
        import requests as _req
    except ImportError:
        raise RuntimeError("Instala 'requests' para descargar flujos n8n: pip install requests")
    url = f"{base_url.rstrip('/')}/api/v1/workflows/{workflow_id}"
    resp = _req.get(url, headers={"X-N8N-API-KEY": api_key}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _extraer_webhook_url(wf: Dict[str, Any], base_url: str) -> str:
    """Construye la URL del webhook a partir del JSON del flujo.

    Soporta el Webhook clasico y el Chat Trigger de langchain.
    """
    base = base_url.rstrip("/")
    for node in wf.get("nodes", []):
        tipo = node.get("type", "")
        params = node.get("parameters", {})
        if tipo == "n8n-nodes-base.webhook":
            path = str(params.get("path", "")).lstrip("/")
            if path:
                return f"{base}/webhook/{path}"
        if tipo == "@n8n/n8n-nodes-langchain.chatTrigger":
            wid = node.get("webhookId") or str(params.get("path", "")).lstrip("/")
            if wid:
                return f"{base}/webhook/{wid}/chat"
    return ""


def cargar_workflow_desde_n8n(entrada: str) -> Tuple[Optional[Dict[str, Any]], str, str]:
    """Descarga un flujo n8n desde una URL del editor (o ID) usando la API.

    Usa N8N_BASE_URL y N8N_API_KEY del .env. La base de la URL pasada tiene
    prioridad sobre N8N_BASE_URL.

    Retorna (workflow, base_url_usada, webhook_url_derivada).
    workflow es None si fallo la descarga.
    """
    try:
        base_extraida, wf_id = _parsear_url_workflow(entrada)
    except ValueError as exc:
        _print_error(str(exc))
        return None, "", ""

    base_url = base_extraida or os.getenv("N8N_BASE_URL", "")
    api_key = os.getenv("N8N_API_KEY", "")

    if not base_url:
        _print_error(
            "No hay URL base de n8n. Pasa una URL completa "
            "(https://tu-n8n.com/workflow/ID) o configura N8N_BASE_URL en .env"
        )
        return None, "", ""
    if not api_key:
        _print_error("Falta N8N_API_KEY en .env para descargar el flujo via API de n8n")
        return None, "", ""

    try:
        wf = _descargar_workflow_n8n(base_url, api_key, wf_id)
    except Exception as exc:
        _print_error(f"No se pudo descargar el flujo '{wf_id}' desde n8n: {exc}")
        return None, "", ""

    if "nodes" not in wf:
        _print_error("La respuesta de la API de n8n no contiene 'nodes'. ¿ID o permisos correctos?")
        return None, "", ""

    webhook_url = _extraer_webhook_url(wf, base_url)
    return wf, base_url, webhook_url


def cargar_json(ruta: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _print_error(f"JSON invalido: {exc}")
        return None
    if "nodes" not in data:
        _print_error("El archivo no contiene la clave 'nodes'. ¿Es un flujo n8n exportado?")
        return None
    return data


def _print_error(msg: str) -> None:
    if HAS_RICH:
        console.print(f"[red]ERROR: {msg}[/red]")
    else:
        print(f"ERROR: {msg}")


def _print_ok(msg: str) -> None:
    if HAS_RICH:
        console.print(f"[green]OK[/green] {msg}")
    else:
        print(f"OK: {msg}")


def _spin(msg: str, fn, *args, **kwargs):
    if HAS_RICH:
        with Progress(SpinnerColumn(), TextColumn(f"[cyan]{msg}"), console=console, transient=True) as p:
            p.add_task("", total=None)
            result = fn(*args, **kwargs)
        return result
    print(f"  {msg}")
    return fn(*args, **kwargs)


def _ask(prompt_text: str, default: str = "") -> str:
    if HAS_RICH:
        val = console.input(f"[cyan]{prompt_text}[/cyan]")
    else:
        val = input(prompt_text)
    return val.strip() or default


try:
    from juez.evaluation.utils.review import (
        revisar_reglas_negocio as _revisar_reglas_negocio,
        configurar_evaluacion_conversacional as _configurar_evaluacion_conv,
    )
    _TIENE_REVISOR = True
except Exception:
    _TIENE_REVISOR = False


def revisar_reglas_negocio(reglas: Dict, openai_key: str = "") -> Dict:
    if _TIENE_REVISOR:
        return _revisar_reglas_negocio(reglas, openai_key=openai_key)
    return reglas


def configurar_evaluacion_conversacional(openai_key: str = "") -> Dict:
    if _TIENE_REVISOR:
        return _configurar_evaluacion_conv(openai_key=openai_key)
    raw = input("Numero de conversaciones (5-50) [10]: ").strip()
    try:
        total = max(5, min(50, int(raw))) if raw else 10
    except ValueError:
        total = 10
    return {"total": total, "distribucion": None, "escenarios_extra": [], "concurrencia": min(max(total // 4, 2), 8)}


def mostrar_resumen(analisis: Dict, salida: Path) -> None:
    problemas = analisis["problemas"]
    altos = [p for p in problemas if p["severidad"] in ("CRITICO", "ALTO")]
    m = analisis["metricas"]
    rag = analisis["rag"]

    if HAS_RICH:
        t = Table(title="Resumen del analisis", border_style="cyan", show_header=True)
        t.add_column("Metrica", style="dim")
        t.add_column("Valor", justify="right", style="bold")

        t.add_row("Nodos funcionales", str(m["nodos_funcionales"]))
        t.add_row("  + Tools del agente", str(m["nodos_tools"]))
        t.add_row("  + Satelites", str(m["nodos_satelite"]))
        t.add_row("  + Sticky Notes", str(m["sticky_notes"]))
        t.add_row("  = Total en JSON", str(m["total_nodos_json"]))
        t.add_row("Nodos IA/LangChain", str(m["nodos_ia"]))
        t.add_row("APIs externas", str(m["apis_externas"]))
        t.add_row("Tipo de RAG", rag["descripcion_tipo"][:40])
        t.add_row(
            "Problemas detectados",
            f"[red]{len(problemas)}[/red]" if problemas else "[green]0[/green]",
        )
        t.add_row(
            "Altos/Criticos",
            f"[bold red]{len(altos)}[/bold red]" if altos else "[green]0[/green]",
        )
        t.add_row("Redundancias", str(len(analisis["redundancias"])))
        console.print(t)

        if altos:
            console.print("\n[bold red]Problemas de alta prioridad:[/bold red]")
            for p in altos:
                console.print(f"  [red]*[/red] [{p['severidad']}] {p['descripcion']}")

        console.print(f"\n[bold green]Reporte guardado en:[/bold green] [cyan]{salida}[/cyan]\n")
    else:
        print(f"\nNodos: {m['total_nodos_json']}  |  Problemas: {len(problemas)}  |  Altos: {len(altos)}")
        print(f"OK Reporte guardado en: {salida}")


# =============================================================================
# SCORE N8N
# =============================================================================

# Versión del esquema de scoring del Juez. Cualquier cambio en cómo se
# calculan dimensiones, pesos o severidades debe subir este número y resetear
# (o filtrar por versión) el benchmark histórico.
JUEZ_VERSION = 2

# Mapeo exclusivo: cada tipo de problema pertenece a UNA sola dimensión.
# Mantener este mapeo sincronizado con los `tipo` que emiten los detectores
# en este archivo y en validar_y_enriquecer_modelos().
TIPO_A_DIMENSION: Dict[str, str] = {
    "Seguridad":         "seguridad",
    "Configuracion":     "tools_integraciones",
    "Configuracion IA":  "tools_integraciones",
    "Alineacion Tools":  "calidad_prompt",
    "Resiliencia":       "observabilidad",
    "Flujo":             "observabilidad",
    "Schema Output":     "calidad_prompt",
    "RAG / Retrieval":   "calidad_prompt",
    "Mantenibilidad":    "mantenibilidad",
}

# Penalización por severidad (se aplica una sola vez por problema, sobre la
# dimensión que le corresponde según TIPO_A_DIMENSION).
_PENALIZACION_POR_SEVERIDAD: Dict[str, int] = {
    "CRITICO": 25,
    "ALTO":    15,
    "MEDIO":   8,
    "BAJO":    3,
}

# Pesos del score_general. Solo cuentan las dimensiones que se evaluaron en
# esta corrida: si una está ausente (p.ej. artefacto o evaluacion_viva sin
# datos), su peso se redistribuye proporcionalmente.
_PESOS_DIMENSION: Dict[str, float] = {
    "seguridad":          0.25,
    "tools_integraciones":0.20,
    "observabilidad":     0.15,
    "calidad_prompt":     0.15,
    "mantenibilidad":     0.10,
    "artefacto":          0.05,
    "evaluacion_viva":    0.10,
}


def calcular_score_n8n(
    analisis: Dict[str, Any],
    batch_result=None,
    artefacto_score: Optional[float] = None,
) -> Dict[str, Any]:
    """Calcula el scorecard del flujo n8n.

    Reglas clave (Juez v2):
      - Cada problema penaliza UNA sola dimensión (sin doble conteo).
      - El score_general es el promedio ponderado de las dimensiones evaluadas.
        Las dimensiones sin datos no entran en el promedio.
      - QA de artefacto y evaluación viva sí contribuyen al score_general si
        están presentes.
    """
    problemas = analisis.get("problemas", [])

    # ── Penalización por dimensión (exclusiva, sin doble conteo) ──────────
    pen_por_dim: Dict[str, int] = {
        "seguridad": 0,
        "tools_integraciones": 0,
        "observabilidad": 0,
        "calidad_prompt": 0,
        "mantenibilidad": 0,
    }
    for p in problemas:
        dim = TIPO_A_DIMENSION.get(p.get("tipo", ""), "calidad_prompt")
        peso = _PENALIZACION_POR_SEVERIDAD.get(p.get("severidad", "BAJO"), 3)
        pen_por_dim[dim] = pen_por_dim.get(dim, 0) + peso

    dimensiones: Dict[str, float] = {
        dim: round(max(0.0, 100.0 - pen), 1)
        for dim, pen in pen_por_dim.items()
    }

    # ── Evaluación viva (contra-agente) ───────────────────────────────────
    score_viva: Optional[float] = None
    por_categoria: Dict[str, float] = {}
    if batch_result:
        score_viva = round(getattr(batch_result, "pass_rate", 0.0) * 100, 1)
        for cat, data in getattr(batch_result, "by_category", {}).items():
            total_cat = data.get("total", 0)
            passed_cat = data.get("passed", 0)
            por_categoria[cat] = round(passed_cat / total_cat * 100, 1) if total_cat else 0.0

    # ── QA de artefacto ───────────────────────────────────────────────────
    score_artefacto: Optional[float] = round(artefacto_score, 1) if artefacto_score is not None else None

    # ── Score general: promedio ponderado de dimensiones presentes ────────
    presentes: Dict[str, float] = dict(dimensiones)
    if score_viva is not None:
        presentes["evaluacion_viva"] = score_viva
    if score_artefacto is not None:
        presentes["artefacto"] = score_artefacto

    peso_total = sum(_PESOS_DIMENSION[d] for d in presentes if d in _PESOS_DIMENSION)
    if peso_total > 0:
        score_general = round(
            sum(presentes[d] * _PESOS_DIMENSION[d] for d in presentes if d in _PESOS_DIMENSION)
            / peso_total,
            1,
        )
    else:
        score_general = 0.0

    return {
        "juez_version":        JUEZ_VERSION,
        "score_general":       score_general,
        "seguridad":           dimensiones["seguridad"],
        "tools_integraciones": dimensiones["tools_integraciones"],
        "observabilidad":      dimensiones["observabilidad"],
        "calidad_prompt":      dimensiones["calidad_prompt"],
        "mantenibilidad":      dimensiones["mantenibilidad"],
        "evaluacion_viva":     score_viva if score_viva is not None else 0.0,
        "artefacto":           score_artefacto if score_artefacto is not None else 0.0,
        # Campo dejado por compatibilidad con dashboards externos: no se usa
        # en el cómputo y los voicebots tienen su evaluación propia.
        "config_voz":          0.0,
        "por_categoria":       por_categoria,
    }


def validar_y_enriquecer_modelos(analisis: Dict[str, Any], openai_key: str) -> Dict[str, Any]:
    """Valida dinámicamente los modelos LLM usados en el flujo contra la API real.

    - Si el modelo responde con éxito → válido, sin penalización.
    - Si la API dice explícitamente que no existe → ALTO en problemas.
    - Para rate limit, timeout u otros errores → se asume válido (beneficio de la duda).
    """
    if not openai_key or not HAS_OPENAI:
        return analisis

    modelos_usados: List[str] = list(analisis.get("metricas", {}).get("modelos_llm", []))
    if not modelos_usados:
        return analisis

    client = OpenAI(api_key=openai_key)
    modelos_invalidos: Dict[str, str] = {}

    for modelo in set(modelos_usados):
        if not modelo or modelo == "?":
            continue
        try:
            client.chat.completions.create(
                model=modelo,
                messages=[{"role": "user", "content": "ok"}],
                max_tokens=1,
            )
            # Modelo válido — no hacer nada
        except Exception as exc:
            exc_str = str(exc).lower()
            # Solo marcar inválido si la API lo confirma explícitamente
            if any(phrase in exc_str for phrase in (
                "model_not_found", "does not exist", "no such model",
                "invalid_model", "model not found",
            )):
                modelos_invalidos[modelo] = str(exc)[:200]
            # Rate limit, timeout, auth errors → asumir válido

    analisis["validacion_modelos"] = {
        m: ("invalid" if m in modelos_invalidos else "valid")
        for m in set(modelos_usados)
    }

    if modelos_invalidos:
        modelos_por_nodo: Dict[str, List[str]] = analisis.get("metricas", {}).get("modelos_por_nodo", {})
        for modelo, _error in modelos_invalidos.items():
            nodos_afectados = modelos_por_nodo.get(modelo, [])
            for nodo in (nodos_afectados or ["(nodo desconocido)"]):
                analisis["problemas"].append({
                    "tipo": "Configuracion",
                    "descripcion": (
                        f"Modelo '{modelo}' en '{nodo}' no existe en la API. "
                        "La API confirmo que el modelo no esta disponible — "
                        "verifica el nombre exacto o cambia el modelo."
                    ),
                    "nodo": nodo,
                    "severidad": "ALTO",
                })

    return analisis


# =============================================================================
# MAIN
# =============================================================================

def _run_audit_real_inventario(inventario_id: int) -> int:
    """Short-circuit: audita un PDF YA generado en Drive sin tocar el contra-agente.

    Devuelve exit code: 0 si OK/WARN, 1 si FAIL/UNVERIFIABLE o si hubo error.
    """
    from juez.evaluation.contra_agente.audit_real_pdf import audit_real_inventario

    if HAS_RICH:
        console.print(f"[bold]Auditando inventario real id={inventario_id}[/bold]")
        console.print("[dim]Modo audit-real: no se ejecuta contra-agente, "
                      "solo se lee BD + se llama al Verificador con source=drive[/dim]")
    else:
        print(f"Auditando inventario real id={inventario_id}")
        print("Modo audit-real: no se ejecuta contra-agente, "
              "solo se lee BD + se llama al Verificador con source=drive")

    out = audit_real_inventario(inventario_id)

    if out.get("error"):
        if HAS_RICH:
            console.print(f"[red]Error:[/red] {out['error']}")
        else:
            print(f"Error: {out['error']}")
        return 1

    verdict = (out.get("verdict") or "UNVERIFIABLE").upper()
    score = out.get("score")
    score_str = f"{float(score):.0%}" if score is not None else "n/a"

    if HAS_RICH:
        color = {"OK": "green", "WARN": "yellow", "FAIL": "red"}.get(verdict, "red")
        console.print(
            f"[bold {color}]Verdict: {verdict}[/bold {color}]  "
            f"score={score_str}  "
            f"pdf_drive_file_id={out.get('pdf_drive_file_id')}"
        )
    else:
        print(f"Verdict: {verdict}  score={score_str}  "
              f"pdf_drive_file_id={out.get('pdf_drive_file_id')}")

    for check in (out.get("checks") or [])[:10]:
        name = check.get("name", "?")
        v = check.get("verdict", "?")
        sc = check.get("score")
        sc_str = f"{float(sc):.0%}" if sc is not None else "n/a"
        print(f"  - {name}: {v} score={sc_str}")

    for issue in (out.get("issues") or [])[:5]:
        print(f"    issue {issue.get('severidad', '?')}: "
              f"{str(issue.get('mensaje', ''))[:140]}")

    return 0 if verdict in ("OK", "WARN") else 1


def main() -> None:
    # ── Argumentos posicionales: archivo [webhook_url] [total_conversaciones] ──
    # Filtrar flags de modo para no confundirlos con args posicionales
    import argparse
    parser = argparse.ArgumentParser(prog="evaluar_n8n.py", add_help=False)
    parser.add_argument("--ci-mode",      action="store_true",
                        help="Sale con exit code 1 si el score regresa respecto al run anterior")
    parser.add_argument("--ci-threshold", type=float, default=5.0, metavar="PUNTOS")
    parser.add_argument("--validacion",   action="store_true")
    parser.add_argument("--completo",     action="store_true")
    parser.add_argument("--e2e",           action="store_true",
                        help="Activa una auditoria sintetica de artefacto via Verificador")
    parser.add_argument("--e2e-cases",     type=int, default=1, metavar="K",
                        help="Cantidad de conversaciones marcadas para e2e (default: 1)")
    parser.add_argument("--e2e-model",     default=os.getenv("JUEZ_E2E_MODEL", "gpt-4o-mini"))
    parser.add_argument("--e2e-real-inventario-id", type=int, default=None, metavar="N",
                        help="Lee datos reales de la BD productiva (read-only) para el snapshot "
                             "esperado. Si no se pasa, se usa snapshot sintetico determinístico.")
    parser.add_argument("--audit-real-inventario-id", type=int, default=None, metavar="N",
                        help="Audita el PDF YA generado de un inventario real (read-only): lee la "
                             "BD productiva, resuelve el drive_file_id del ultimo pdf_aprobacion y "
                             "lo despacha al Verificador. NO ejecuta el contra-agente.")
    args, argv_pos = parser.parse_known_args()

    # ── Short-circuit: audit-real-inventario-id ──────────────────────────────
    # Bypassea el menu interactivo y el contra-agente. Solo lee BD + Verificador.
    if args.audit_real_inventario_id is not None:
        banner()
        exit_code = _run_audit_real_inventario(args.audit_real_inventario_id)
        sys.exit(exit_code)

    banner()
    modo_cfg = _seleccionar_modo_analisis()
    modo = modo_cfg["modo"]

    # ── Merge: el menu interactivo gana cuando el CLI no fue explícito ─────────
    # Si el usuario eligió e2e en el menú, activarlo aunque no haya pasado --e2e.
    # Si el menú devolvió valores específicos (cases, real_inv_id), respetarlos.
    if modo_cfg.get("e2e") and not args.e2e:
        args.e2e = True
    if modo_cfg.get("e2e_cases") and modo_cfg["e2e_cases"] > 0:
        # solo si el menu produjo un valor concreto (no señal -1)
        args.e2e_cases = modo_cfg["e2e_cases"]
    if modo_cfg.get("e2e_real_inventario_id") is not None and args.e2e_real_inventario_id is None:
        args.e2e_real_inventario_id = modo_cfg["e2e_real_inventario_id"]

    webhook_url_arg = argv_pos[1] if len(argv_pos) > 1 else ""
    total_conv_arg = int(argv_pos[2]) if len(argv_pos) > 2 else None

    # ── Resolucion de la entrada: URL del editor n8n, ID puro o archivo local ──
    ruta: Optional[Path] = None
    wf: Optional[Dict[str, Any]] = None
    ruta_label = ""
    webhook_auto = ""

    if argv_pos:
        entrada = argv_pos[0].strip()
        if entrada.startswith("http://") or entrada.startswith("https://"):
            # URL del editor de n8n -> descargar el JSON via API
            wf, _base_n8n, webhook_auto = _spin(
                "Descargando flujo desde n8n...",
                cargar_workflow_desde_n8n,
                entrada,
            )
            if wf is None:
                sys.exit(1)
            _print_ok(f"Flujo descargado via API de n8n  (id: {wf.get('id', '?')})")
            if webhook_auto:
                _print_ok(f"Webhook derivado del flujo: {webhook_auto}")
            ruta_label = entrada
        else:
            ruta = Path(entrada)
            if not ruta.exists():
                _print_error(f"Archivo no encontrado: {ruta}")
                sys.exit(1)
    else:
        for _ in range(3):
            ruta = pedir_archivo()
            if ruta:
                break
        if ruta is None:
            _print_error("No se proporciono un archivo valido.")
            sys.exit(1)

    if wf is None:
        wf = cargar_json(ruta)
        if wf is None:
            sys.exit(1)
        ruta_label = str(ruta)

    nombre = wf.get("name") or (ruta.stem if ruta else "flujo_n8n")
    n_total = len(wf.get("nodes", []))
    if HAS_RICH:
        console.print(f"[bold]Flujo cargado:[/bold] {nombre}  ({n_total} nodos en JSON)")
    else:
        print(f"Flujo cargado: {nombre}  ({n_total} nodos en JSON)")

    analisis = _spin("Ejecutando analisis estatico...", lambda: N8nAnalyzer(wf).analizar())
    _print_ok("Analisis estatico completado")

    # ── Modo validacion: estatico + health check real de conexiones ──────────
    if modo == "validacion":
        if HAS_RICH:
            console.print("[dim]Modo validacion — GPT y contra-agente omitidos[/dim]")

        webhook_url_val = webhook_url_arg or webhook_auto or _pedir_webhook_url()

        health: Dict[str, Any] = {}
        if webhook_url_val or analisis.get("herramientas") or analisis.get("apis"):
            health = _spin(
                "Verificando conectividad de endpoints...",
                health_check_n8n,
                webhook_url_val,
                analisis,
            )
            n_ok   = sum(1 for v in health.values() if v.get("status") == "HEALTHY")
            n_down = sum(1 for v in health.values() if v.get("status") == "DOWN")
            n_skip = sum(1 for v in health.values() if v.get("status") == "SKIPPED")
            if n_down == 0:
                _print_ok(f"Health check: {n_ok} HEALTHY, {n_skip} skipped")
            else:
                if HAS_RICH:
                    console.print(f"[yellow]Health check: {n_ok} HEALTHY, {n_down} DOWN, {n_skip} skipped[/yellow]")
                else:
                    print(f"Health check: {n_ok} HEALTHY, {n_down} DOWN, {n_skip} skipped")
        else:
            if HAS_RICH:
                console.print("[dim]Sin endpoints que verificar (no hay tools ni webhook configurado)[/dim]")

        gpt_result: Dict[str, str] = {"omitido": "Modo validacion — solo analisis estatico"}
        reporte_estatico = generar_reporte(analisis, gpt_result, nombre, ruta_label)
        reporte_health   = generar_reporte_health(health, nombre)

        outputs = Path("outputs")
        outputs.mkdir(exist_ok=True)
        nombre_limpio = "".join(c for c in nombre if c.isalnum() or c in " _-").strip().replace(" ", "_")[:50]
        ts_label = datetime.now().strftime("%Y%m%d_%H%M%S")
        salida = outputs / f"n8n_validacion_{nombre_limpio}_{ts_label}.txt"
        salida.write_text(reporte_estatico + "\n\n" + reporte_health, encoding="utf-8")
        mostrar_resumen(analisis, salida)
        return

    # ── Modo completo: GPT + contra-agente ───────────────────────────────────
    if os.getenv("OPENAI_API_KEY"):
        gpt_result = _spin(
            "Analizando con GPT (prompts, tools, schema, arquitectura)...",
            analizar_con_gpt,
            analisis,
            nombre,
        )
        _print_ok("Analisis GPT completado")
        analisis["reglas_negocio"] = gpt_result.get("reglas_negocio", {})
    else:
        if HAS_RICH:
            console.print("[yellow]OPENAI_API_KEY no encontrada — analisis GPT omitido[/yellow]")
        else:
            print("OPENAI_API_KEY no encontrada — analisis GPT omitido")
        gpt_result = {"omitido": "OPENAI_API_KEY no configurada"}

    # ── Revisión interactiva de reglas de negocio ─────────────────────────────
    if modo != "validacion":
        analisis["reglas_negocio"] = revisar_reglas_negocio(
            analisis.get("reglas_negocio", {}),
            openai_key=os.getenv("OPENAI_API_KEY", ""),
        )

    reporte_estatico = generar_reporte(analisis, gpt_result, nombre, ruta_label)

    # ── Health check (también en modo completo) ───────────────────────────────
    webhook_url = webhook_url_arg or webhook_auto or _pedir_webhook_url()
    health: Dict[str, Any] = {}
    if webhook_url or analisis.get("herramientas") or analisis.get("apis"):
        health = _spin("Verificando conectividad de endpoints...", health_check_n8n, webhook_url, analisis)
        n_ok   = sum(1 for v in health.values() if v.get("status") == "HEALTHY")
        n_deg  = sum(1 for v in health.values() if v.get("status") == "DEGRADED")
        n_down = sum(1 for v in health.values() if v.get("status") == "DOWN")
        if n_down == 0:
            _print_ok(f"Health check: {n_ok} HEALTHY, {n_deg} DEGRADED")
        else:
            if HAS_RICH:
                console.print(f"[yellow]Health check: {n_ok} HEALTHY, {n_deg} DEGRADED, {n_down} DOWN[/yellow]")
            else:
                print(f"Health check: {n_ok} HEALTHY, {n_deg} DEGRADED, {n_down} DOWN")
    reporte_health = generar_reporte_health(health, nombre)

    reporte_ca = ""
    batch_result_n8n = None
    if HAS_CONTRA_AGENTE:
        if not webhook_url:
            webhook_url = _pedir_webhook_url()
        if webhook_url:
            openai_key_n8n = os.getenv("OPENAI_API_KEY", "")
            if total_conv_arg is None:
                cfg_n8n      = configurar_evaluacion_conversacional(openai_key=openai_key_n8n)
                total_conv   = cfg_n8n["total"]
                dist_n8n     = cfg_n8n.get("distribucion")
                esc_n8n      = cfg_n8n.get("escenarios_extra", [])
                concurrencia = cfg_n8n["concurrencia"]
            else:
                total_conv   = total_conv_arg
                dist_n8n     = None
                esc_n8n      = []
                concurrencia = max(2, min(total_conv // 4, 8))
            e2e_cases = max(0, args.e2e_cases if args.e2e else 0)
            if e2e_cases and not openai_key_n8n:
                e2e_cases = 0
                if HAS_RICH:
                    console.print("[yellow]E2E omitido: falta OPENAI_API_KEY para MockAgent[/yellow]")
                else:
                    print("E2E omitido: falta OPENAI_API_KEY para MockAgent")
            if e2e_cases and not _verificador_healthcheck():
                e2e_cases = 0
                if HAS_RICH:
                    console.print("[yellow]E2E omitido: Verificador no responde al healthcheck[/yellow]")
                else:
                    print("E2E omitido: Verificador no responde al healthcheck")
            _print_ok(f"Iniciando contra-agente  ({total_conv} conversaciones, concurrencia={concurrencia})")
            try:
                batch_result_n8n, reporte_ca = ejecutar_contra_agente(
                    analisis_n8n=analisis,
                    webhook_url=webhook_url,
                    agent_name=nombre,
                    total_conv=total_conv,
                    concurrencia=concurrencia,
                    distribucion_override=dist_n8n,
                    escenarios_extra=esc_n8n,
                    e2e_cases=e2e_cases,
                    e2e_model=args.e2e_model,
                    e2e_real_inventario_id=args.e2e_real_inventario_id,
                )
                _print_ok("Contra-agente completado")
            except Exception as exc:
                reporte_ca = f"\n[CONTRA-AGENTE — Error durante la ejecucion: {exc}]\n"
                _print_error(f"Contra-agente fallo: {exc}")
        else:
            if HAS_RICH:
                console.print("[dim]Contra-agente omitido (sin URL de webhook)[/dim]")
            else:
                print("Contra-agente omitido (sin URL de webhook)")
    else:
        if HAS_RICH:
            console.print("[yellow]Modulos evaluation/ no encontrados — contra-agente omitido[/yellow]")

    # ── QA de artefacto (aditivo: solo si el agente tiene spec) ────────────────
    agent_id_n8n = nombre.lower().replace(" ", "_")[:40]
    _artef: Dict[str, Any] = {}
    try:
        from juez.evaluation.artifact import run_artifact_eval
        _artef = run_artifact_eval(agent_id_n8n)
        if _artef:
            # Los defectos reales del artefacto penalizan el score via la logica existente
            analisis.setdefault("problemas", []).extend(_artef.get("problemas", []))
            _print_ok(f"QA de artefacto completado  (score {_artef.get('score_artefacto', 0.0):.1f})")
    except Exception as exc:
        _artef = {}
        if HAS_RICH:
            console.print(f"[yellow]QA de artefacto omitido: {exc}[/yellow]")

    # ── Historial, benchmark y recomendaciones ────────────────────────────────
    _artef_score = _artef.get("score_artefacto") if _artef else None
    _scores_n8n = calcular_score_n8n(analisis, batch_result_n8n, artefacto_score=_artef_score)
    if _artef:
        # También se expone en por_categoria para que el reporte de benchmark
        # lo muestre en su sección de categorías (además de contar en el
        # score_general vía la dimensión 'artefacto').
        _scores_n8n.setdefault("por_categoria", {})["artefacto"] = _artef.get("score_artefacto", 0.0)
    domain_n8n = analisis.get("dominio", "") or analisis.get("descripcion_dominio", "") or ""

    try:
        from juez.evaluation.history import store as hist_store
        from juez.evaluation.benchmark import store as bench_store
        from juez.evaluation.recommendations import generar_recomendaciones

        _snapshot   = hist_store.build_snapshot(agent_id_n8n, nombre, _scores_n8n, analisis, batch_result_n8n)
        hist_store.guardar(agent_id_n8n, _snapshot)
        _anterior   = hist_store.cargar_anterior(agent_id_n8n)
        _comparacion = hist_store.generar_seccion_comparacion(_snapshot, _anterior)

        bench_store.guardar_entrada(agent_id_n8n, nombre, domain_n8n, _scores_n8n)
        _benchmark  = bench_store.generar_seccion_benchmark(_scores_n8n, domain=domain_n8n)

        _recomendaciones = generar_recomendaciones(
            scores=_scores_n8n,
            batch_result=batch_result_n8n,
            analisis=analisis,
            openai_key=os.getenv("OPENAI_API_KEY", ""),
        )
    except Exception as exc:
        _comparacion = f"\n  [Historial no disponible: {exc}]\n"
        _benchmark   = ""
        _recomendaciones = ""

    outputs = Path("outputs")
    outputs.mkdir(exist_ok=True)
    nombre_limpio = "".join(c for c in nombre if c.isalnum() or c in " _-").strip().replace(" ", "_")[:50]
    ts_label = datetime.now().strftime("%Y%m%d_%H%M%S")
    salida = outputs / f"n8n_eval_{nombre_limpio}_{ts_label}.txt"

    partes = [_comparacion, reporte_estatico]
    if _benchmark:
        partes.append(_benchmark)
    if _recomendaciones:
        partes.append(_recomendaciones)
    partes.append(reporte_health)
    if reporte_ca:
        partes.append(reporte_ca)
    if _artef and _artef.get("reporte"):
        partes.append(_artef["reporte"])
    salida.write_text("\n\n".join(partes), encoding="utf-8")

    mostrar_resumen(analisis, salida)

    # ── Modo CI/CD ────────────────────────────────────────────────────────────
    if args.ci_mode:
        score_actual   = _scores_n8n.get("score_general", 0.0)
        score_anterior = (_anterior or {}).get("score_general", None)
        threshold      = args.ci_threshold
        print("\n" + "=" * 60)
        print("  CI/CD MODE")
        print("=" * 60)
        print(f"  Score actual   : {score_actual:.1f}%")
        if score_anterior is None:
            print("  Score anterior : (primera evaluacion — sin baseline)")
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
