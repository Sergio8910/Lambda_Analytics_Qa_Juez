"""Verificación SINTÉTICA de objetivos de un flujo n8n.

Responde a la pregunta: *"¿este flujo está construido para cumplir su objetivo?"*
(ej. generar un ticket, enviar un correo) **sin disparar ni enviar nada**.

Es 100% sintético: solo lee el JSON exportado del flujo y "recorre" el grafo en
seco. Para cada objetivo declarado verifica que:

  1. Existe al menos un nodo que cumple ese objetivo (por tipo de nodo y/o
     contenido de sus parámetros).
  2. Ese nodo está habilitado (no `disabled`).
  3. Es alcanzable desde un trigger/webhook (no es una rama huérfana).
  4. Está configurado (destinatario del correo, URL del ticket, credenciales…).

LÍMITE HONESTO: confirma que el flujo está *diseñado* para cumplir el objetivo;
NO garantiza el runtime en vivo (una credencial vencida o una API caída solo se
ven ejecutando). Es la capa de QA estática equivalente a "lintar" el flujo.

Paquete ADITIVO: no toca el core del Juez ni el framework de artefactos (que sí
dispara). Reusa `parse_workflow` y el modelo `N8nFinding` para integrarse al
resto del análisis de n8n sin fricción.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from .models import FindingSeverity, N8nFinding
from .parser import iter_parameter_strings, parse_workflow

ObjectiveKind = Literal[
    "send_email",
    "create_ticket",
    "http_request",
    "db_write",
    "db_read",
    "generate_file",
    "respond_webhook",
    "ai_response",
    "send_message",
    "custom",
]

ObjectiveStatus = Literal["cumplido", "parcial", "incumplido"]


# =============================================================================
# Heurísticas por tipo de objetivo (todo por substrings sobre node_type / params)
# =============================================================================


class _KindSpec:
    """Cómo se reconoce y qué se le exige a un objetivo de un `kind` conocido."""

    def __init__(
        self,
        type_tokens: Tuple[str, ...] = (),
        param_tokens: Tuple[str, ...] = (),
        recipient_tokens: Tuple[str, ...] = (),
        required_params: Tuple[str, ...] = (),
        needs_credentials: bool = False,
        label: str = "",
    ) -> None:
        # Tokens que, si aparecen en node_type, marcan al nodo como candidato.
        self.type_tokens = type_tokens
        # Tokens que, si aparecen en CUALQUIER parámetro, también lo marcan candidato.
        self.param_tokens = param_tokens
        # Al menos UNO de estos debe estar presente y no vacío (ej. destinatario).
        self.recipient_tokens = recipient_tokens
        # TODOS estos deben estar presentes y no vacíos.
        self.required_params = required_params
        self.needs_credentials = needs_credentials
        self.label = label


KIND_SPECS: Dict[str, _KindSpec] = {
    "send_email": _KindSpec(
        type_tokens=("gmail", "emailsend", "sendemail", "microsoftoutlook",
                     "sendgrid", "mailgun", "awsses"),
        recipient_tokens=("to", "toemail", "sendto", "recipient", "toaddress",
                          "toaddresses", "email"),
        needs_credentials=True,
        label="enviar correo",
    ),
    "create_ticket": _KindSpec(
        type_tokens=("jira", "zendesk", "freshdesk", "servicenow", "hubspot",
                     "linear", "zammad"),
        param_tokens=("ticket", "issue"),
        needs_credentials=True,
        label="crear ticket",
    ),
    "http_request": _KindSpec(
        type_tokens=("httprequest",),
        required_params=("url",),
        label="llamada HTTP",
    ),
    "db_write": _KindSpec(
        type_tokens=("postgres", "mysql", "mongodb", "supabase", "microsoftsql",
                     "snowflake", "redis", "questdb", "cratedb"),
        param_tokens=("insert", "upsert", "update", "executequery"),
        needs_credentials=True,
        label="escritura en base de datos",
    ),
    "db_read": _KindSpec(
        type_tokens=("postgres", "mysql", "mongodb", "supabase", "microsoftsql",
                     "snowflake", "redis", "questdb", "cratedb"),
        param_tokens=("select", "find", "get", "executequery"),
        needs_credentials=True,
        label="lectura de base de datos",
    ),
    "generate_file": _KindSpec(
        type_tokens=("converttofile", "readwritefile", "writebinaryfile",
                     "movebinarydata", "spreadsheetfile", "html"),
        param_tokens=("pdf", "binary", "tofile", "csv", "xlsx"),
        label="generar archivo",
    ),
    "respond_webhook": _KindSpec(
        type_tokens=("respondtowebhook",),
        label="responder al webhook",
    ),
    "ai_response": _KindSpec(
        type_tokens=("openai", "langchain", "anthropic", "agent", "chatmodel",
                     "lmchat", "assistant"),
        needs_credentials=True,
        label="respuesta de IA",
    ),
    "send_message": _KindSpec(
        type_tokens=("telegram", "slack", "whatsapp", "twilio", "discord",
                     "microsoftteams"),
        recipient_tokens=("chatid", "to", "channel", "phonenumber", "recipient",
                          "channelid"),
        needs_credentials=True,
        label="enviar mensaje",
    ),
    "custom": _KindSpec(label="objetivo personalizado"),
}


# =============================================================================
# Modelos de entrada/salida
# =============================================================================


class Objective(BaseModel):
    """Un objetivo declarado del flujo (qué define que cumplió su propósito)."""

    id: str = Field(..., description="Slug del objetivo, ej. 'crear_ticket'")
    descripcion: str = Field("", description="Descripción legible del objetivo")
    kind: ObjectiveKind = Field(
        "custom",
        description="Tipo conocido de objetivo. 'custom' usa solo los matchers de abajo.",
    )
    # Matchers extra / overrides (útiles para 'custom' o para afinar un kind).
    node_type_contains: List[str] = Field(
        default_factory=list,
        description="Tokens que deben aparecer en el node_type para considerar un nodo candidato.",
    )
    param_contains: List[str] = Field(
        default_factory=list,
        description="Substrings que deben aparecer en los parámetros del nodo candidato (todos).",
    )
    required_params: List[str] = Field(
        default_factory=list,
        description="Claves de parámetro que deben estar presentes y no vacías (todas).",
    )
    requires_credentials: Optional[bool] = Field(
        None,
        description="Override de si el nodo necesita credenciales. None = usar el default del kind.",
    )
    min_count: int = Field(1, ge=1, description="Mínimo de nodos que deben cumplir el objetivo.")
    severity_if_missing: FindingSeverity = Field(
        "high",
        description="Severidad del finding si el objetivo no tiene nodo.",
    )

    model_config = {"extra": "forbid"}


class ObjectiveCheck(BaseModel):
    """Resultado de verificar un objetivo concreto."""

    id: str
    descripcion: str
    kind: str
    status: ObjectiveStatus
    score: float
    matched_nodes: List[str] = Field(default_factory=list)
    reachable_nodes: List[str] = Field(default_factory=list)
    findings: List[N8nFinding] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ObjectivesReport(BaseModel):
    """Reporte sintético de cumplimiento de objetivos del flujo."""

    version: str = "n8n-objectives-v1"
    workflow_name: str
    total_objetivos: int
    cumplidos: int
    parciales: int
    incumplidos: int
    score_global: float
    veredicto: Literal["cumple", "cumple_parcial", "no_cumple"]
    objetivos: List[ObjectiveCheck] = Field(default_factory=list)
    findings: List[N8nFinding] = Field(default_factory=list)
    nota_metodo: str = (
        "Verificación 100% sintética: se analiza el JSON del flujo y se recorre el "
        "grafo en seco, SIN ejecutar nodos ni llamar servicios externos. Confirma "
        "que el flujo está diseñado para cumplir el objetivo (nodo presente, "
        "alcanzable, habilitado y configurado); no garantiza el runtime en vivo."
    )

    model_config = {"extra": "forbid"}


# =============================================================================
# Núcleo
# =============================================================================

_STATUS_SCORE = {"cumplido": 100.0, "parcial": 60.0, "incumplido": 0.0}


def _node_param_blob(raw_parameters: Dict[str, Any]) -> str:
    """Aplana todos los strings de parámetros en un solo blob en minúsculas."""
    partes: List[str] = []
    for _path, value in iter_parameter_strings(raw_parameters):
        partes.append(value)
    return " \n ".join(partes).lower()


def _param_present(raw_parameters: Dict[str, Any], token: str) -> bool:
    """¿Existe un parámetro cuyo path contiene `token` y tiene valor no vacío?

    Una expresión n8n (`={{ ... }}`) cuenta como presente.
    """
    token = token.lower()
    for path, value in iter_parameter_strings(raw_parameters):
        if token not in path.lower():
            continue
        v = (value or "").strip()
        if not v:
            continue
        # Expresión n8n vacía tipo "={{}}" no cuenta.
        if v in ("={{}}", "={{ }}", "{{}}"):
            continue
        return True
    return False


def _matches_objective(node: Any, obj: Objective, spec: _KindSpec) -> bool:
    """¿El nodo es candidato a cumplir el objetivo? (solo lectura, sin ejecutar)."""
    node_type = node.node_type.lower()
    blob = _node_param_blob(node.raw_parameters)

    # 1) ¿Match por tipo de nodo? (tokens del kind + overrides del objetivo)
    type_tokens = list(spec.type_tokens) + [t.lower() for t in obj.node_type_contains]
    type_match = any(tok in node_type for tok in type_tokens) if type_tokens else False

    # 2) ¿Match por contenido de parámetros? (tokens del kind)
    param_token_match = any(tok in blob for tok in spec.param_tokens) if spec.param_tokens else False

    # Para 'ai_response' la categoría ya clasificada también vale.
    if obj.kind == "ai_response" and node.category == "ai":
        type_match = True

    candidate = type_match or param_token_match
    if not candidate:
        return False

    # 3) Si el objetivo exige substrings de parámetros, TODOS deben estar.
    if obj.param_contains:
        if not all(tok.lower() in blob for tok in obj.param_contains):
            return False

    return True


def _config_findings(node: Any, obj: Objective, spec: _KindSpec) -> Tuple[List[N8nFinding], bool, bool]:
    """Chequeos de configuración del nodo candidato.

    Devuelve (findings, falta_config_requerida, falta_credenciales).
    """
    findings: List[N8nFinding] = []
    falta_config = False
    falta_creds = False

    # 3a) Parámetros requeridos explícitos (todos).
    faltantes = [p for p in obj.required_params if not _param_present(node.raw_parameters, p)]
    # 3b) Required del kind (todos).
    faltantes += [p for p in spec.required_params if not _param_present(node.raw_parameters, p)]
    if faltantes:
        falta_config = True
        findings.append(
            N8nFinding(
                finding_id=f"obj-{obj.id}-config-{node.name}".replace(" ", "_")[:80],
                category="logic",
                severity="high",
                title=f"Objetivo '{obj.id}': configuración incompleta",
                message=(
                    f"El nodo '{node.name}' cumpliría el objetivo '{obj.id}' "
                    f"({spec.label or obj.kind}) pero le faltan parámetros requeridos: "
                    f"{', '.join(sorted(set(faltantes)))}."
                ),
                node_names=[node.name],
                evidence={"parametros_faltantes": sorted(set(faltantes))},
                recommendation="Completa esos parámetros en el nodo para que el flujo pueda cumplir el objetivo.",
            )
        )

    # 3c) Destinatario (al menos uno) para correos/mensajes.
    if spec.recipient_tokens:
        tiene_dest = any(_param_present(node.raw_parameters, t) for t in spec.recipient_tokens)
        if not tiene_dest:
            falta_config = True
            findings.append(
                N8nFinding(
                    finding_id=f"obj-{obj.id}-dest-{node.name}".replace(" ", "_")[:80],
                    category="logic",
                    severity="high",
                    title=f"Objetivo '{obj.id}': sin destinatario",
                    message=(
                        f"El nodo '{node.name}' ({spec.label or obj.kind}) no tiene un "
                        f"destinatario configurado (ej. {', '.join(spec.recipient_tokens[:3])})."
                    ),
                    node_names=[node.name],
                    evidence={"destinatarios_buscados": list(spec.recipient_tokens)},
                    recommendation="Configura el destinatario del envío en el nodo.",
                )
            )

    # 3d) Credenciales.
    needs_creds = obj.requires_credentials if obj.requires_credentials is not None else spec.needs_credentials
    if needs_creds and not node.has_credentials:
        falta_creds = True
        findings.append(
            N8nFinding(
                finding_id=f"obj-{obj.id}-creds-{node.name}".replace(" ", "_")[:80],
                category="security",
                severity="medium",
                title=f"Objetivo '{obj.id}': nodo sin credenciales",
                message=(
                    f"El nodo '{node.name}' ({spec.label or obj.kind}) no tiene credenciales "
                    f"asociadas en el export. Podría fallar en ejecución."
                ),
                node_names=[node.name],
                evidence={},
                recommendation="Verifica que el nodo tenga credenciales válidas asignadas.",
            )
        )

    return findings, falta_config, falta_creds


def _reachable_set(graph: Any) -> Tuple[set, bool]:
    """Nodos alcanzables desde los triggers. (set, hay_triggers)."""
    if not graph or not graph.trigger_nodes:
        return set(), False
    adjacency: Dict[str, List[str]] = defaultdict(list)
    for edge in graph.edges:
        adjacency[edge.source].append(edge.target)
    visited: set = set()
    queue = deque(graph.trigger_nodes)
    while queue:
        cur = queue.popleft()
        if cur in visited:
            continue
        visited.add(cur)
        for nxt in adjacency.get(cur, []):
            if nxt not in visited:
                queue.append(nxt)
    return visited, True


def _check_one(obj: Objective, nodes: List[Any], reachable: set, hay_triggers: bool) -> ObjectiveCheck:
    spec = KIND_SPECS.get(obj.kind, KIND_SPECS["custom"])
    matches = [n for n in nodes if _matches_objective(n, obj, spec)]
    enabled = [n for n in matches if not n.disabled]
    # Si no hay triggers detectados, no podemos juzgar alcanzabilidad → tratamos
    # a los habilitados como alcanzables (y el análisis estático ya flagea el
    # "sin trigger" por su cuenta).
    reachable_nodes = [n for n in enabled if (not hay_triggers or n.name in reachable)]

    findings: List[N8nFinding] = []

    # Caso 1: no hay ningún nodo que cumpla el objetivo.
    if len(matches) < obj.min_count:
        findings.append(
            N8nFinding(
                finding_id=f"obj-{obj.id}-missing".replace(" ", "_")[:80],
                category="structure",
                severity=obj.severity_if_missing,
                title=f"Objetivo '{obj.id}' sin nodo que lo cumpla",
                message=(
                    f"No se encontró ningún nodo que cumpla el objetivo '{obj.id}' "
                    f"({spec.label or obj.kind}). El flujo, tal como está, no puede cumplirlo."
                ),
                node_names=[],
                evidence={"encontrados": len(matches), "requeridos": obj.min_count},
                recommendation=f"Agrega un nodo que realice: {obj.descripcion or spec.label or obj.kind}.",
            )
        )
        return ObjectiveCheck(
            id=obj.id, descripcion=obj.descripcion, kind=obj.kind, status="incumplido",
            score=0.0, matched_nodes=[n.name for n in matches], reachable_nodes=[], findings=findings,
        )

    # Caso 2: hay nodos pero todos deshabilitados.
    if not enabled:
        findings.append(
            N8nFinding(
                finding_id=f"obj-{obj.id}-disabled".replace(" ", "_")[:80],
                category="structure",
                severity="high",
                title=f"Objetivo '{obj.id}': nodo deshabilitado",
                message=(
                    f"El/los nodo(s) que cumplirían el objetivo '{obj.id}' están deshabilitados "
                    f"({', '.join(n.name for n in matches)})."
                ),
                node_names=[n.name for n in matches],
                evidence={},
                recommendation="Habilita el nodo para que el flujo pueda cumplir el objetivo.",
            )
        )
        return ObjectiveCheck(
            id=obj.id, descripcion=obj.descripcion, kind=obj.kind, status="incumplido",
            score=0.0, matched_nodes=[n.name for n in matches], reachable_nodes=[], findings=findings,
        )

    # Caso 3: hay nodos habilitados pero ninguno alcanzable desde el trigger.
    if not reachable_nodes:
        findings.append(
            N8nFinding(
                finding_id=f"obj-{obj.id}-unreachable".replace(" ", "_")[:80],
                category="structure",
                severity="high",
                title=f"Objetivo '{obj.id}': nodo inalcanzable",
                message=(
                    f"El/los nodo(s) del objetivo '{obj.id}' existen pero no son alcanzables "
                    f"desde ningún trigger/webhook ({', '.join(n.name for n in enabled)}). "
                    f"El flujo nunca llegaría a ejecutarlos."
                ),
                node_names=[n.name for n in enabled],
                evidence={},
                recommendation="Conecta el nodo al flujo principal que parte del trigger.",
            )
        )
        return ObjectiveCheck(
            id=obj.id, descripcion=obj.descripcion, kind=obj.kind, status="incumplido",
            score=0.0, matched_nodes=[n.name for n in matches],
            reachable_nodes=[], findings=findings,
        )

    # Caso 4: hay nodos alcanzables. El objetivo se cumple si AL MENOS UNO está
    # bien configurado (no el primero que aparezca). Esto evita falsos negativos
    # cuando hay varios nodos del mismo tipo (ej. Telegram: nodos de descarga +
    # el de envío real) — solo uno necesita cumplir la config del objetivo.
    evaluaciones = [
        (_config_findings(n, obj, spec), n) for n in reachable_nodes
    ]
    # Preferimos: 1) un nodo totalmente OK, 2) uno con config OK (creds puede
    # faltar), 3) el primero como fallback.
    elegido = next(
        ((cf, n) for cf, n in evaluaciones if not cf[1] and not cf[2]), None
    )
    if elegido is None:
        elegido = next(((cf, n) for cf, n in evaluaciones if not cf[1]), None)
    if elegido is None:
        elegido = evaluaciones[0]

    (cfg_findings, falta_config, falta_creds), candidato = elegido
    findings.extend(cfg_findings)

    if falta_config:
        status: ObjectiveStatus = "incumplido"
    elif falta_creds:
        status = "parcial"
    else:
        status = "cumplido"

    return ObjectiveCheck(
        id=obj.id, descripcion=obj.descripcion, kind=obj.kind, status=status,
        score=_STATUS_SCORE[status], matched_nodes=[n.name for n in matches],
        reachable_nodes=[n.name for n in reachable_nodes], findings=findings,
    )


def verify_objectives(workflow: Dict[str, Any], objectives: List[Objective]) -> ObjectivesReport:
    """Verifica sintéticamente que un flujo n8n cumple sus objetivos declarados.

    No ejecuta nada: solo analiza el JSON exportado del flujo.

    Args:
        workflow: JSON del workflow exportado de n8n (con `nodes` y `connections`).
        objectives: lista de objetivos declarados.

    Returns:
        ObjectivesReport con el veredicto, score y findings por objetivo.
    """
    inventory, graph = parse_workflow(workflow)
    reachable, hay_triggers = _reachable_set(graph)
    nodes = list(graph.nodes)

    checks = [_check_one(obj, nodes, reachable, hay_triggers) for obj in objectives]

    cumplidos = sum(1 for c in checks if c.status == "cumplido")
    parciales = sum(1 for c in checks if c.status == "parcial")
    incumplidos = sum(1 for c in checks if c.status == "incumplido")

    score_global = round(sum(c.score for c in checks) / len(checks), 1) if checks else 0.0
    if incumplidos:
        veredicto = "no_cumple"
    elif parciales:
        veredicto = "cumple_parcial"
    else:
        veredicto = "cumple"

    all_findings = [f for c in checks for f in c.findings]

    return ObjectivesReport(
        workflow_name=inventory.workflow_name,
        total_objetivos=len(checks),
        cumplidos=cumplidos,
        parciales=parciales,
        incumplidos=incumplidos,
        score_global=score_global,
        veredicto=veredicto,
        objetivos=checks,
        findings=all_findings,
    )
