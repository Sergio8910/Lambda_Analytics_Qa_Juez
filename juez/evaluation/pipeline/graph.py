"""
evaluation/pipeline/graph.py
────────────────────────────
Construye el grafo de conexiones entre agentes de un pipeline multi-agente.
Cada nodo puede ser un agente ElevenLabs o un flujo n8n.

Uso típico
----------
    from juez.evaluation.pipeline.graph import build_pipeline_graph

    graph = build_pipeline_graph([
        {
            "node_id": "abc123",
            "node_type": "elevenlabs",
            "name": "Agente de Ventas",
            "analisis": { ... },
            "raw_flow": None,
            "scores": {},
            "batch_result": None,
        },
        {
            "node_id": "crm_flow",
            "node_type": "n8n",
            "name": "CRM Connector",
            "analisis": { ... },
            "raw_flow": { ... },
            "scores": {},
            "batch_result": None,
        },
    ])

    print(graph.order)          # orden topológico
    print(graph.edges)          # conexiones detectadas
    print(graph.gaps)           # salidas sin receptor
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Dominios / hosts que se filtran porque no son parte del pipeline interno
# ---------------------------------------------------------------------------
_EXTERNAL_HOSTS = frozenset(
    {
        "api.openai.com",
        "api.elevenlabs.io",
        "api.anthropic.com",
        "s3.amazonaws.com",
        "storage.googleapis.com",
        "www.googleapis.com",
        "oauth2.googleapis.com",
        "accounts.google.com",
        "graph.microsoft.com",
        "login.microsoftonline.com",
        "api.sendgrid.com",
        "api.twilio.com",
        "api.stripe.com",
        "hooks.slack.com",
        "api.slack.com",
        "smtp.sendgrid.net",
    }
)

_INTERNAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PipelineNode:
    node_id: str           # agent_id (eleven) o nombre normalizado (n8n)
    node_type: str         # "elevenlabs" o "n8n"
    name: str
    entry_urls: List[str]  # URLs/paths que este nodo escucha
    exit_urls: List[str]   # URLs que este nodo llama hacia afuera
    analisis: Dict[str, Any]
    scores: Dict[str, Any] = field(default_factory=dict)
    batch_result: Any = None


@dataclass
class PipelineEdge:
    source_id: str
    target_id: str
    source_url: str   # URL de salida del source
    target_url: str   # URL de entrada del target que hizo match
    match_type: str   # "exact", "path", "fuzzy", "inferred"
    confidence: float # 0.0 – 1.0


@dataclass
class PipelineGap:
    node_id: str
    exit_url: str   # URL de salida sin receptor conocido
    description: str


# ---------------------------------------------------------------------------
# PipelineGraph
# ---------------------------------------------------------------------------


class PipelineGraph:
    """
    Grafo dirigido de nodos del pipeline.

    Atributos públicos
    ------------------
    nodes   : dict {node_id -> PipelineNode}
    edges   : list[PipelineEdge]
    gaps    : list[PipelineGap]  – salidas sin receptor
    cycles  : list[list[str]]    – ciclos detectados (listas de node_ids)
    order   : list[str]          – orden topológico (Kahn's algorithm)
    """

    def __init__(self, nodes: List[PipelineNode]) -> None:
        self.nodes: Dict[str, PipelineNode] = {n.node_id: n for n in nodes}
        self.edges: List[PipelineEdge] = []
        self.gaps: List[PipelineGap] = []
        self.cycles: List[List[str]] = []
        self.order: List[str] = []
        self._build()

    # ------------------------------------------------------------------
    # Construcción
    # ------------------------------------------------------------------

    def _build(self) -> None:
        """
        Pasos:
        1. Detectar edges por matching de URLs entre exit_urls y entry_urls.
        2. Detectar gaps (exit_urls sin receptor).
        3. Detectar ciclos en el grafo resultante.
        4. Calcular orden topológico (Kahn's).
        """
        matched_exits: Dict[str, set] = defaultdict(set)  # node_id -> {exit_url, ...}

        # ── paso 1: detectar edges ──────────────────────────────────────
        for src_id, src_node in self.nodes.items():
            for exit_url in src_node.exit_urls:
                best_edge: Optional[PipelineEdge] = None
                best_confidence = 0.0

                for tgt_id, tgt_node in self.nodes.items():
                    if tgt_id == src_id:
                        continue
                    for entry_url in tgt_node.entry_urls:
                        matched, match_type, confidence = self._urls_match(
                            exit_url, entry_url
                        )
                        if matched and confidence > best_confidence:
                            best_confidence = confidence
                            best_edge = PipelineEdge(
                                source_id=src_id,
                                target_id=tgt_id,
                                source_url=exit_url,
                                target_url=entry_url,
                                match_type=match_type,
                                confidence=confidence,
                            )

                if best_edge is not None:
                    self.edges.append(best_edge)
                    matched_exits[src_id].add(exit_url)

        # ── paso 2: detectar gaps ───────────────────────────────────────
        for src_id, src_node in self.nodes.items():
            for exit_url in src_node.exit_urls:
                if exit_url not in matched_exits[src_id]:
                    self.gaps.append(
                        PipelineGap(
                            node_id=src_id,
                            exit_url=exit_url,
                            description=(
                                f"No se encontró receptor para la URL de salida "
                                f"'{exit_url}' del nodo '{src_node.name}'"
                            ),
                        )
                    )

        # ── paso 3: detectar ciclos (DFS) ───────────────────────────────
        self.cycles = self._detect_cycles()

        # ── paso 4: orden topológico ────────────────────────────────────
        self.order = self._topological_sort()

    # ------------------------------------------------------------------
    # Helpers de URL
    # ------------------------------------------------------------------

    def _extract_path(self, url: str) -> str:
        """
        Extrae y normaliza el path de una URL.
        Normalización: lowercase, strip trailing slash.
        """
        url = url.strip()
        try:
            parsed = urlparse(url)
            # Si no tiene scheme, urlparse puede meter todo en path
            path = parsed.path if parsed.scheme else urlparse("http://" + url).path
        except Exception:
            path = url
        return path.lower().rstrip("/")

    def _urls_match(
        self, exit_url: str, entry_url: str
    ) -> Tuple[bool, str, float]:
        """
        Compara dos URLs y retorna (match, match_type, confidence).

        Estrategias
        -----------
        exact  : URLs idénticas (case-insensitive, trailing slash ignorado) → 1.0
        path   : mismo path, diferente dominio                               → 0.9
        fuzzy  : un path es substring del otro                               → 0.6
        """
        # ── exact ───────────────────────────────────────────────────────
        norm_exit = exit_url.lower().strip().rstrip("/")
        norm_entry = entry_url.lower().strip().rstrip("/")
        if norm_exit == norm_entry:
            return True, "exact", 1.0

        # ── path ────────────────────────────────────────────────────────
        path_exit = self._extract_path(exit_url)
        path_entry = self._extract_path(entry_url)

        if path_exit and path_entry and path_exit == path_entry:
            return True, "path", 0.9

        # ── fuzzy (substring) ───────────────────────────────────────────
        if path_exit and path_entry:
            if path_exit in path_entry or path_entry in path_exit:
                return True, "fuzzy", 0.6

        return False, "", 0.0

    # ------------------------------------------------------------------
    # Detección de ciclos (DFS)
    # ------------------------------------------------------------------

    def _detect_cycles(self) -> List[List[str]]:
        """
        Detecta todos los ciclos simples en el grafo usando DFS con
        seguimiento de pila de recursión.
        Retorna una lista de ciclos, cada uno como lista de node_ids.
        """
        adj: Dict[str, List[str]] = defaultdict(list)
        for edge in self.edges:
            adj[edge.source_id].append(edge.target_id)

        visited: set = set()
        rec_stack: List[str] = []
        rec_set: set = set()
        cycles: List[List[str]] = []

        def dfs(node: str) -> None:
            visited.add(node)
            rec_stack.append(node)
            rec_set.add(node)

            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in rec_set:
                    # Encontramos un ciclo; extraemos desde donde empieza
                    cycle_start = rec_stack.index(neighbor)
                    cycle = rec_stack[cycle_start:] + [neighbor]
                    # Evitar duplicados exactos
                    if cycle not in cycles:
                        cycles.append(cycle)

            rec_stack.pop()
            rec_set.discard(node)

        for node_id in self.nodes:
            if node_id not in visited:
                dfs(node_id)

        return cycles

    # ------------------------------------------------------------------
    # Orden topológico (Kahn's algorithm)
    # ------------------------------------------------------------------

    def _topological_sort(self) -> List[str]:
        """
        Algoritmo de Kahn.
        Si hay ciclos, los nodos participantes se añaden al final en orden
        arbitrario para que el resultado siempre cubra todos los nodos.
        """
        in_degree: Dict[str, int] = {nid: 0 for nid in self.nodes}
        adj: Dict[str, List[str]] = defaultdict(list)

        for edge in self.edges:
            adj[edge.source_id].append(edge.target_id)
            in_degree[edge.target_id] += 1

        queue: deque = deque(
            [nid for nid, deg in in_degree.items() if deg == 0]
        )
        # Orden determinista: ordenar por node_id para estabilidad
        queue = deque(sorted(queue))

        result: List[str] = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for neighbor in sorted(adj[node]):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # Nodos no procesados (participan en ciclos) → añadir al final
        remaining = sorted(nid for nid in self.nodes if nid not in result)
        result.extend(remaining)

        return result

    # ------------------------------------------------------------------
    # Consultas sobre el grafo
    # ------------------------------------------------------------------

    def entry_nodes(self) -> List[PipelineNode]:
        """Nodos sin aristas entrantes — punto de inicio del pipeline."""
        nodes_with_incoming = {e.target_id for e in self.edges}
        return [
            node
            for nid, node in self.nodes.items()
            if nid not in nodes_with_incoming
        ]

    def exit_nodes(self) -> List[PipelineNode]:
        """Nodos sin aristas salientes — punto final del pipeline."""
        nodes_with_outgoing = {e.source_id for e in self.edges}
        return [
            node
            for nid, node in self.nodes.items()
            if nid not in nodes_with_outgoing
        ]

    def single_points_of_failure(self) -> List[str]:
        """
        Node IDs que, si fallan, bloquean todos los nodos siguientes
        (puntos de articulación en el grafo dirigido).

        Un nodo es SPOF si es el único camino entre algún nodo de entrada
        y algún nodo de salida, es decir, si su eliminación desconecta al
        menos un nodo de entrada de al menos un nodo de salida.
        """
        if len(self.nodes) <= 1:
            return []

        adj: Dict[str, List[str]] = defaultdict(list)
        for edge in self.edges:
            adj[edge.source_id].append(edge.target_id)

        entry_ids = {n.node_id for n in self.entry_nodes()}
        exit_ids = {n.node_id for n in self.exit_nodes()}

        def reachable_from(start: str, exclude: Optional[str] = None) -> set:
            """BFS desde `start` sin pasar por `exclude`."""
            visited: set = set()
            queue: deque = deque([start])
            while queue:
                cur = queue.popleft()
                if cur in visited or cur == exclude:
                    continue
                visited.add(cur)
                for nb in adj.get(cur, []):
                    if nb not in visited and nb != exclude:
                        queue.append(nb)
            return visited

        # Verificar si sin la exclusión algún exit es alcanzable desde algún entry
        baseline_pairs: set = set()
        for eid in entry_ids:
            reachable = reachable_from(eid)
            for xid in exit_ids:
                if xid in reachable:
                    baseline_pairs.add((eid, xid))

        if not baseline_pairs:
            return []

        spofs: List[str] = []
        for candidate in list(self.nodes.keys()):
            # Omitir nodos que son ellos mismos entry o exit (su fallo es obvio)
            broken = False
            for eid, xid in baseline_pairs:
                if eid == candidate or xid == candidate:
                    # Si el entry o exit mismo falla, todos los caminos que
                    # dependen de él se rompen → es SPOF
                    broken = True
                    break
                reachable = reachable_from(eid, exclude=candidate)
                if xid not in reachable:
                    broken = True
                    break
            if broken:
                spofs.append(candidate)

        return spofs


# ---------------------------------------------------------------------------
# Funciones auxiliares de extracción
# ---------------------------------------------------------------------------


def _is_external_url(url: str) -> bool:
    """
    Retorna True si la URL pertenece a un servicio externo conocido
    o a una dirección interna (localhost, 127.x.x.x) que no es pipeline.
    """
    url = url.strip()
    if not url:
        return True
    try:
        parsed = urlparse(url if "://" in url else "http://" + url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return False

    if host in _INTERNAL_HOSTS:
        return True
    # Comparación exacta y por sufijo de dominio
    for ext in _EXTERNAL_HOSTS:
        if host == ext or host.endswith("." + ext):
            return True
    return False


def extract_entry_urls(
    node_type: str, analisis: Dict[str, Any], raw_flow: Dict[str, Any] = None
) -> List[str]:
    """
    Extrae los puntos de entrada (URLs que este nodo escucha).

    ElevenLabs
    ----------
    No tiene URL de entrada propia (recibe llamadas de voz).
    Retorna ["elevenlabs://voice/{agent_id}"] como identificador conceptual.

    n8n
    ---
    Extrae paths de nodos Webhook.
    1. Si raw_flow está disponible, lee directamente los nodos con
       type == "n8n-nodes-base.webhook" y toma parameters.path.
    2. Si no, usa analisis["flujo"]["entry_points"] si existe.
    """
    if node_type == "elevenlabs":
        agent_id = analisis.get("agent_id", "unknown")
        return [f"elevenlabs://voice/{agent_id}"]

    if node_type == "n8n":
        urls: List[str] = []

        # ── opción 1: raw_flow disponible ────────────────────────────
        if raw_flow:
            nodes = raw_flow.get("nodes", [])
            for node in nodes:
                if node.get("type") == "n8n-nodes-base.webhook":
                    params = node.get("parameters", {})
                    path = params.get("path", "")
                    if path:
                        # Normalizar: asegurar que empieza con /
                        if not path.startswith("/"):
                            path = "/" + path
                        urls.append(path)

        # ── opción 2: analisis["flujo"]["entry_points"] ──────────────
        if not urls:
            flujo = analisis.get("flujo", {})
            entry_points = flujo.get("entry_points", [])
            for ep in entry_points:
                if isinstance(ep, str) and ep:
                    urls.append(ep)
                elif isinstance(ep, dict):
                    # Puede tener "path", "url" o "endpoint"
                    for key in ("url", "path", "endpoint"):
                        val = ep.get(key, "")
                        if val:
                            urls.append(val)
                            break

        return urls

    # Tipo desconocido: sin entradas
    return []


def extract_exit_urls(node_type: str, analisis: Dict[str, Any]) -> List[str]:
    """
    Extrae las URLs de salida (URLs que este nodo llama hacia afuera).

    ElevenLabs
    ----------
    analisis["tools"] donde tool["url"] existe y no está vacío.
    Solo se incluyen tools de tipo webhook/http (o sin tipo definido).

    n8n
    ---
    analisis["apis"] (llamadas HTTP) + analisis["herramientas"] donde haya URL.
    Se filtran URLs vacías, internas (localhost) y de terceros conocidos.
    """
    urls: List[str] = []

    if node_type == "elevenlabs":
        tools = analisis.get("tools", [])
        for tool in tools:
            url = (tool.get("url") or "").strip()
            if url and not _is_external_url(url):
                urls.append(url)

    elif node_type == "n8n":
        # ── APIs (llamadas HTTP salientes) ───────────────────────────
        apis = analisis.get("apis", [])
        for api in apis:
            url = (api.get("url") or "").strip()
            if url and not _is_external_url(url):
                urls.append(url)

        # ── Herramientas con URL ─────────────────────────────────────
        herramientas = analisis.get("herramientas", [])
        for tool in herramientas:
            url = (tool.get("url") or "").strip()
            if url and not _is_external_url(url):
                urls.append(url)

    # Deduplicar preservando orden
    seen: set = set()
    result: List[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------


def build_pipeline_graph(nodes_data: List[Dict[str, Any]]) -> PipelineGraph:
    """
    Construye un PipelineGraph a partir de una lista de descriptores de nodo.

    Cada elemento de `nodes_data` debe tener:
    {
        "node_id"     : str,
        "node_type"   : "elevenlabs" | "n8n",
        "name"        : str,
        "analisis"    : Dict,
        "raw_flow"    : Dict | None,   # para n8n: JSON original del flujo
        "scores"      : Dict,
        "batch_result": Any,
    }
    """
    nodes: List[PipelineNode] = []

    for d in nodes_data:
        node_type = d["node_type"]
        analisis = d.get("analisis", {})
        raw_flow = d.get("raw_flow")

        entry_urls = extract_entry_urls(node_type, analisis, raw_flow)
        exit_urls = extract_exit_urls(node_type, analisis)

        nodes.append(
            PipelineNode(
                node_id=d["node_id"],
                node_type=node_type,
                name=d["name"],
                entry_urls=entry_urls,
                exit_urls=exit_urls,
                analisis=analisis,
                scores=d.get("scores", {}),
                batch_result=d.get("batch_result"),
            )
        )

    return PipelineGraph(nodes)
