"""Descubrimiento de recursos relacionados a un agente o branch de ElevenLabs.

Capacidades:

- Detectar el tipo de ID que el cliente pasa (agent_*, agtbrch_*, agtvrsn_*).
- Resolver un branch_id a su agente padre y traer la config del agente bajo ese branch.
- Extraer las URLs salientes (webhook tools) del agente, deduplicadas.
- Matchear esas URLs contra flujos n8n disponibles en una instancia n8n.

Se usa desde api/runner.py cuando el cliente pide evaluar un agente o branch
y opcionalmente quiere incluir los flujos n8n que ese agente llama.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

import requests


# =============================================================================
# DETECCIÓN DE TIPO DE ID
# =============================================================================


def detect_id_type(id_str: str) -> str:
    """Retorna 'agent', 'branch', 'version' o 'unknown' según el prefijo del ID."""
    s = (id_str or "").strip()
    if s.startswith("agent_"):
        return "agent"
    if s.startswith("agtbrch_"):
        return "branch"
    if s.startswith("agtvrsn_"):
        return "version"
    return "unknown"


# =============================================================================
# RESOLUCIÓN DE BRANCH A AGENTE PADRE
# =============================================================================


def resolve_branch(branch_id: str, eleven_key: str, timeout: float = 15.0) -> Dict[str, Any]:
    """Resuelve un branch_id a un dict con:

      - branch_id        : el ID original
      - agent_id         : ID del agente padre
      - branch_name      : nombre legible de la rama
      - branch_data      : dict completo de la rama (incluye versiones recientes)
      - agent_config     : configuración completa del agente bajo ese branch

    Lanza ValueError si el branch no se encuentra en ninguna conversación.
    """
    if not eleven_key:
        raise ValueError("ELEVENLABS_API_KEY no configurada")

    headers = {"xi-api-key": eleven_key}

    # ElevenLabs no expone un GET /branches/{id} sin agent_id. Hay que descubrir
    # el agent_id padre buscando en las conversaciones recientes. Si no aparece
    # ahí, recorremos los agentes del workspace listando sus branches.
    agent_id = _descubrir_agent_id_por_conversaciones(branch_id, eleven_key)

    if not agent_id:
        agent_id = _descubrir_agent_id_por_listado_agentes(branch_id, eleven_key)

    if not agent_id:
        raise ValueError(
            f"No se pudo determinar el agent_id padre del branch '{branch_id}'. "
            "Verifica que el branch exista y que la API key tenga acceso."
        )

    # Detalle del branch
    r = requests.get(
        f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}/branches/{branch_id}",
        headers=headers, timeout=timeout,
    )
    if r.status_code != 200:
        raise ValueError(
            f"No se pudo descargar el branch {branch_id} del agente {agent_id}: "
            f"HTTP {r.status_code} — {r.text[:200]}"
        )
    branch_data = r.json()

    # Config del agente bajo esa rama
    r = requests.get(
        f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}",
        params={"branch_id": branch_id},
        headers=headers, timeout=timeout,
    )
    if r.status_code != 200:
        raise ValueError(
            f"No se pudo descargar el agente {agent_id} bajo branch {branch_id}: "
            f"HTTP {r.status_code} — {r.text[:200]}"
        )
    agent_config = r.json()

    return {
        "branch_id":   branch_id,
        "agent_id":    agent_id,
        "branch_name": branch_data.get("name", ""),
        "branch_data": branch_data,
        "agent_config": agent_config,
    }


def _descubrir_agent_id_por_conversaciones(branch_id: str, eleven_key: str) -> str:
    """Busca el branch_id en conversaciones recientes para mapear a su agent_id."""
    try:
        r = requests.get(
            "https://api.elevenlabs.io/v1/convai/conversations",
            params={"page_size": 100},
            headers={"xi-api-key": eleven_key}, timeout=15,
        )
        if r.status_code != 200:
            return ""
        for c in r.json().get("conversations", []):
            if c.get("branch_id") == branch_id:
                return c.get("agent_id", "")
    except Exception:
        pass
    return ""


def _descubrir_agent_id_por_listado_agentes(branch_id: str, eleven_key: str) -> str:
    """Fallback: recorrer agentes del workspace y listar sus branches para encontrar match."""
    try:
        r = requests.get(
            "https://api.elevenlabs.io/v1/convai/agents",
            headers={"xi-api-key": eleven_key}, timeout=15,
        )
        if r.status_code != 200:
            return ""
        for a in r.json().get("agents", []):
            aid = a.get("agent_id")
            if not aid:
                continue
            rr = requests.get(
                f"https://api.elevenlabs.io/v1/convai/agents/{aid}/branches",
                headers={"xi-api-key": eleven_key}, timeout=15,
            )
            if rr.status_code != 200:
                continue
            for b in rr.json().get("results", []):
                if b.get("id") == branch_id:
                    return aid
    except Exception:
        pass
    return ""


# =============================================================================
# EXTRACCIÓN DE URLs SALIENTES DEL AGENTE
# =============================================================================


def extract_outbound_urls(agent_config: Dict[str, Any], eleven_key: str) -> List[Dict[str, str]]:
    """Extrae las URLs de webhook que el agente puede llamar via sus tools.

    Retorna una lista de dicts: [{"tool": nombre, "url": url}, ...] deduplicados.
    Cubre tanto tools embebidos en conversation_config como tool_ids externos.
    """
    prompt = (
        agent_config.get("conversation_config", {})
        .get("agent", {})
        .get("prompt", {})
    )

    urls: Dict[str, str] = {}  # url -> tool_name

    # Tools embebidos
    for t in prompt.get("tools", []) or []:
        if t.get("type") == "webhook":
            url = (t.get("api_schema") or {}).get("url", "").strip()
            name = t.get("name", "tool")
            if url:
                urls.setdefault(url, name)

    # Tool IDs externos (referencias a tools globales del workspace)
    for tid in prompt.get("tool_ids", []) or []:
        try:
            r = requests.get(
                f"https://api.elevenlabs.io/v1/convai/tools/{tid}",
                headers={"xi-api-key": eleven_key}, timeout=10,
            )
            if r.status_code != 200:
                continue
            tc = r.json().get("tool_config", {})
            if tc.get("type") == "webhook":
                url = (tc.get("api_schema") or {}).get("url", "").strip()
                name = tc.get("name", "tool")
                if url:
                    urls.setdefault(url, name)
        except Exception:
            continue

    return [{"tool": n, "url": u} for u, n in urls.items()]


# =============================================================================
# MATCHING DE URLs SALIENTES A FLUJOS n8n
# =============================================================================


def _path_segments(url: str) -> Tuple[List[str], str]:
    """Retorna (segs_después_de_/webhook(-test)/, prefijo_detectado).

    Ejemplos:
      https://n8n.co/webhook/euro-pickers       -> (["euro-pickers"], "webhook")
      https://n8n.co/webhook-test/abc/def       -> (["abc", "def"], "webhook-test")
      https://n8n.co/foo/bar                    -> (["foo", "bar"], "")
    """
    p = urlparse(url.strip())
    segs = [s for s in p.path.split("/") if s]
    if segs and segs[0] in ("webhook", "webhook-test"):
        return segs[1:], segs[0]
    return segs, ""


def match_urls_to_n8n_flows(
    urls: List[Dict[str, str]],
    n8n_base_url: str,
    n8n_api_key: str,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Para cada URL saliente del agente, busca si existe un flujo n8n que la
    reciba. Retorna:

      {
        "matches": [
          {
            "tool": "...", "url": "...",
            "workflow_id": "...", "workflow_name": "...",
            "active": bool, "webhook_node": "...", "path": "..."
          }, ...
        ],
        "sin_match": [
          { "tool": "...", "url": "..." }, ...
        ],
        "externos": [
          { "tool": "...", "url": "..." }, ...   # URLs que no apuntan al n8n configurado
        ]
      }

    Si n8n_base_url o n8n_api_key están vacíos, retorna todo como "externos".
    """
    out: Dict[str, Any] = {"matches": [], "sin_match": [], "externos": []}

    if not n8n_base_url or not n8n_api_key:
        out["externos"] = list(urls)
        return out

    expected_host = urlparse(n8n_base_url).netloc

    # Separar URLs que sí apuntan a nuestra instancia n8n
    candidatas: List[Dict[str, str]] = []
    for u in urls:
        host = urlparse(u["url"]).netloc
        if host == expected_host:
            candidatas.append(u)
        else:
            out["externos"].append(u)

    if not candidatas:
        return out

    # Descargar lista de flujos UNA vez
    try:
        r = requests.get(
            f"{n8n_base_url}/api/v1/workflows",
            headers={"X-N8N-API-KEY": n8n_api_key}, timeout=timeout,
        )
        flows = r.json().get("data", [])
    except Exception:
        # Si no podemos listar, marcamos todos como sin_match con error
        out["sin_match"] = list(candidatas)
        return out

    # Construir índice path -> [flujos] inspeccionando cada workflow en detalle.
    # Esto es N+1 requests, pero N=100 es manejable en ~10s.
    path_index: Dict[str, List[Dict[str, Any]]] = {}
    for wf_summary in flows:
        wf_id = wf_summary.get("id")
        if not wf_id:
            continue
        try:
            rr = requests.get(
                f"{n8n_base_url}/api/v1/workflows/{wf_id}",
                headers={"X-N8N-API-KEY": n8n_api_key}, timeout=timeout,
            )
            if rr.status_code != 200:
                continue
            full = rr.json()
        except Exception:
            continue
        for node in full.get("nodes", []) or []:
            if node.get("type") == "n8n-nodes-base.webhook":
                p = (node.get("parameters") or {}).get("path", "").strip("/")
                if p:
                    path_index.setdefault(p, []).append({
                        "workflow_id":   wf_id,
                        "workflow_name": full.get("name") or wf_id,
                        "active":        bool(full.get("active", False)),
                        "webhook_node":  node.get("name") or "Webhook",
                        "path":          p,
                    })

    # Match estricto: comparar el path tras /webhook(-test)/ con el path del nodo
    for c in candidatas:
        segs, prefijo = _path_segments(c["url"])
        target_path = "/".join(segs)
        # Match por path completo
        matched = path_index.get(target_path) or []
        # Fallback: probar primer segmento solamente (ej. /webhook/foo/bar -> foo)
        if not matched and segs:
            matched = path_index.get(segs[0]) or []
        if matched:
            for m in matched:
                out["matches"].append({**c, **m})
        else:
            out["sin_match"].append(c)

    return out
