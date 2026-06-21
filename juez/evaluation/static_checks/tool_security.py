"""Chequeos de SEGURIDAD de tools sobre un flujo n8n (inspirado en SkillSpector).

Lee el JSON del flujo (no ejecuta nada) y reporta riesgos de seguridad en cómo
los nodos usan sus tools. Cubre las categorías factibles sobre JSON de n8n:

  - Código peligroso (eval/exec/child_process/comandos de sistema)
  - Secretos hardcodeados (API keys/tokens/passwords literales)
  - SSRF / URLs peligrosas (localhost, IPs internas, metadata cloud, file://)
  - Exfiltración de datos (envío de secretos/credenciales a destinos externos)
  - Excesiva agencia / tool destructiva (DELETE HTTP, DROP/TRUNCATE SQL, comandos)
  - Prompt injection (entrada de usuario cruda inyectada en prompts/args de IA)

Formato de salida común: {"tipo","descripcion","nodo","severidad"} con
severidad CRITICO/ALTO/MEDIO/BAJO — igual que el resto de static_checks.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from juez.evaluation.n8n.parser import iter_parameter_strings

# --- patrones ---------------------------------------------------------------

# Lookbehind (?<![.\w]) evita falsos positivos de métodos como `regex.exec()`.
_CODE_DANGER = [
    (r"(?<![.\w])eval\s*\(", "uso de eval()"),
    (r"(?<![.\w])new\s+Function\s*\(", "uso de new Function()"),
    (r"child_process", "uso de child_process"),
    (r"\.exec\s*\(\s*['\"]", "ejecución de comando vía .exec('...')"),
    (r"execSync\s*\(", "execSync de comando"),
    (r"\bspawn\s*\(", "spawn de proceso"),
    (r"require\s*\(\s*['\"]fs['\"]\s*\)", "acceso al sistema de archivos (fs)"),
    (r"\bos\.system\b", "os.system()"),
    (r"\bsubprocess\b", "subprocess"),
    (r"rm\s+-rf", "borrado recursivo (rm -rf)"),
]

# Secretos: token tras Bearer, claves típicas de proveedores, asignación literal.
_SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9]{20,}", "posible API key de OpenAI (sk-...)"),
    (r"AIza[0-9A-Za-z_\-]{30,}", "posible API key de Google (AIza...)"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "posible token de Slack (xox...)"),
    (r"AKIA[0-9A-Z]{16}", "posible Access Key de AWS (AKIA...)"),
    (r"ghp_[A-Za-z0-9]{30,}", "posible token de GitHub (ghp_...)"),
    (r"Bearer\s+[A-Za-z0-9._\-]{20,}", "token Bearer hardcodeado"),
]

# Hosts peligrosos para SSRF.
_SSRF_PATTERNS = [
    (r"169\.254\.169\.254", "endpoint de metadata cloud (169.254.169.254)", "CRITICO"),
    (r"metadata\.google\.internal", "endpoint de metadata de GCP", "CRITICO"),
    (r"localhost", "localhost", "ALTO"),
    (r"127\.0\.0\.1", "loopback 127.0.0.1", "ALTO"),
    (r"\b0\.0\.0\.0\b", "0.0.0.0", "ALTO"),
    (r"\[::1\]", "loopback IPv6 [::1]", "ALTO"),
    (r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "IP privada (10.x)", "MEDIO"),
    (r"\b192\.168\.\d{1,3}\.\d{1,3}\b", "IP privada (192.168.x)", "MEDIO"),
    (r"\b172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b", "IP privada (172.16-31.x)", "MEDIO"),
    (r"file://", "esquema file://", "ALTO"),
]

# Para EXFILTRACIÓN: términos de datos sensibles ENVIADOS (no incluye
# 'authorization'/'token'/'apikey', que son auth normal en headers y darían
# falsos positivos en cualquier llamada autenticada).
_EXFIL_TERMS = ("password", "passwd", "contraseña", "secret",
                "cedula", "cédula", "dni", "tarjeta", "card_number", "ssn", "cvv")


def _node_blob(node: Dict[str, Any]) -> str:
    partes = [v for _p, v in iter_parameter_strings(node.get("parameters", {}) or {})]
    return "\n".join(partes)


def _body_blob(node: Dict[str, Any]) -> str:
    """Solo los parámetros del CUERPO de la petición (no headers/url)."""
    partes = [
        v for p, v in iter_parameter_strings(node.get("parameters", {}) or {})
        if "body" in p.lower()
    ]
    return "\n".join(partes).lower()


def _t(node_type: str) -> str:
    return (node_type or "").lower()


def _mk(tipo: str, descripcion: str, nodo: str, severidad: str) -> Dict[str, str]:
    return {"tipo": tipo, "descripcion": descripcion, "nodo": nodo, "severidad": severidad}


def check_tool_security(workflow: Dict[str, Any]) -> List[Dict[str, str]]:
    """Audita la seguridad de los nodos/tools de un flujo n8n. Solo lectura."""
    problemas: List[Dict[str, str]] = []
    nodes = workflow.get("nodes") or []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or "(sin nombre)")
        ntype = _t(node.get("type", ""))
        blob = _node_blob(node)
        blob_low = blob.lower()

        # 1) Código peligroso ------------------------------------------------
        if any(tok in ntype for tok in ("code", "function", "executecommand")):
            for patron, etiqueta in _CODE_DANGER:
                if re.search(patron, blob, re.IGNORECASE):
                    problemas.append(_mk(
                        "Seguridad / Código", f"Código potencialmente peligroso: {etiqueta}.",
                        name, "ALTO"))
            if "executecommand" in ntype:
                problemas.append(_mk(
                    "Seguridad / Agencia", "Nodo ejecuta comandos del sistema operativo.",
                    name, "ALTO"))

        # 2) Secretos hardcodeados ------------------------------------------
        for patron, etiqueta in _SECRET_PATTERNS:
            if re.search(patron, blob):
                problemas.append(_mk(
                    "Seguridad / Secretos", f"Secreto hardcodeado en el nodo: {etiqueta}.",
                    name, "ALTO"))
                break  # un finding por nodo basta

        # 3) SSRF / URLs peligrosas -----------------------------------------
        if "httprequest" in ntype or "http" in ntype:
            for patron, etiqueta, sev in _SSRF_PATTERNS:
                if re.search(patron, blob, re.IGNORECASE):
                    problemas.append(_mk(
                        "Seguridad / SSRF", f"URL apunta a destino peligroso: {etiqueta}.",
                        name, sev))
                    break

            # 4) Exfiltración: manda datos sensibles en el CUERPO a un destino externo
            params = node.get("parameters", {}) or {}
            method = str(params.get("method", "")).upper()
            body = _body_blob(node)
            envia_sensible = any(term in body for term in _EXFIL_TERMS)
            if envia_sensible and method in ("POST", "PUT", "PATCH"):
                problemas.append(_mk(
                    "Seguridad / Exfiltración",
                    "El nodo HTTP envía datos sensibles (contraseñas/PII) en el cuerpo hacia un destino externo.",
                    name, "MEDIO"))

            # 5a) Excesiva agencia: DELETE HTTP -------------------------------
            if method == "DELETE":
                problemas.append(_mk(
                    "Seguridad / Agencia", "Operación HTTP destructiva (DELETE).",
                    name, "MEDIO"))

        # 5b) SQL destructivo ------------------------------------------------
        if any(tok in ntype for tok in ("postgres", "mysql", "mssql", "mongodb", "sql")):
            if re.search(r"\bdrop\s+table\b|\btruncate\b", blob_low) or \
               (re.search(r"\bdelete\s+from\b", blob_low) and "where" not in blob_low):
                problemas.append(_mk(
                    "Seguridad / Agencia",
                    "Sentencia SQL destructiva (DROP/TRUNCATE o DELETE sin WHERE).",
                    name, "ALTO"))

        # 6) Prompt injection (heurístico) ----------------------------------
        # Solo nodos de LLM/agente reales (no memoria/parser/embeddings/vectorstore)
        es_llm = any(tok in ntype for tok in ("agent", "openai", "chatmodel", "lmchat", "gemini", "anthropic")) \
            and not any(tok in ntype for tok in ("memory", "parser", "embedding", "vectorstore", "retriever"))
        if es_llm:
            # Buscar input externo crudo SOLO en campos de prompt/mensaje/texto.
            for path, val in iter_parameter_strings(node.get("parameters", {}) or {}):
                if not any(k in path.lower() for k in ("prompt", "text", "message", "system", "instruction")):
                    continue
                if re.search(r"\{\{[^}]*(\$json|\.body|\.message|\.text|\.chatInput|webhook)[^}]*\}\}", val, re.IGNORECASE):
                    problemas.append(_mk(
                        "Seguridad / Prompt Injection",
                        "Entrada externa (usuario/webhook) inyectada directamente en el prompt de IA sin sanitizar — posible prompt injection.",
                        name, "MEDIO"))
                    break

    return problemas


def check_tool_security_eleven(tools: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Chequeos de seguridad sobre las tools (webhooks) de un agente ElevenLabs.

    `tools` es la lista normalizada de `_tools()` de evaluar_elevenlabs: cada
    tool trae `nombre`, `url`, `metodo`, `campos_requeridos`, `campos_opcionales`.
    Reusa los mismos patrones que el chequeo de n8n. (La detección de credenciales
    hardcodeadas en el body ya la hace evaluar_elevenlabs; aquí cubrimos SSRF,
    secretos en la URL, agencia excesiva y exfiltración por nombre de campo.)
    """
    problemas: List[Dict[str, str]] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        name = str(t.get("nombre") or "(sin nombre)")
        url = str(t.get("url") or "")
        metodo = str(t.get("metodo") or "").upper()

        # SSRF / URL peligrosa
        for patron, etiqueta, sev in _SSRF_PATTERNS:
            if re.search(patron, url, re.IGNORECASE):
                problemas.append(_mk(
                    "Seguridad / SSRF", f"La tool apunta a un destino peligroso: {etiqueta}.",
                    name, sev))
                break

        # Secreto embebido en la URL
        for patron, etiqueta in _SECRET_PATTERNS:
            if re.search(patron, url):
                problemas.append(_mk(
                    "Seguridad / Secretos", f"Secreto hardcodeado en la URL de la tool: {etiqueta}.",
                    name, "ALTO"))
                break

        # Excesiva agencia: método destructivo
        if metodo == "DELETE":
            problemas.append(_mk(
                "Seguridad / Agencia", "La tool usa una operación HTTP destructiva (DELETE).",
                name, "MEDIO"))

        # Exfiltración: la tool recoge/envía campos sensibles a un webhook externo
        campos = [str(c).lower() for c in (t.get("campos_requeridos", []) + t.get("campos_opcionales", []))]
        sensibles = [c for c in campos if any(term in c for term in _EXFIL_TERMS)]
        if sensibles and url.startswith("http"):
            problemas.append(_mk(
                "Seguridad / Exfiltración",
                f"La tool envía campos sensibles ({', '.join(sorted(set(sensibles)))}) a un webhook externo.",
                name, "MEDIO"))

    return problemas
