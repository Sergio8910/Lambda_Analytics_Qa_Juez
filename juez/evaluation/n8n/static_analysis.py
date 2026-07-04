from __future__ import annotations

import re
from collections import Counter, defaultdict
from statistics import mean
from typing import Any, Dict, Iterable, List

from .models import N8nFinding, N8nScorecard, N8nWorkflowAnalysis
from .parser import iter_parameter_strings, parse_workflow

_SECRET_KEYWORDS = (
    "token",
    "apikey",
    "api_key",
    "secret",
    "password",
    "authorization",
    "clientsecret",
    "accesskey",
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9._\-]{8,}\.[A-Za-z0-9._\-]{8,}"),
)
_NODE_REF_PATTERNS = (
    re.compile(r"""\$\(['"]([^'"]+)['"]\)"""),
    re.compile(r"""\$node\[['"]([^'"]+)['"]\]"""),
)
_PROMPT_FIELD_HINTS = ("prompt", "instruction", "systemmessage", "usermessage", "template", "text")
_RISKY_CATEGORIES = {"http", "code", "ai", "subworkflow"}


def analyze_workflow(workflow: Dict[str, Any], include_graph: bool = True) -> N8nWorkflowAnalysis:
    inventory, graph = parse_workflow(workflow)
    findings: List[N8nFinding] = []
    node_lookup = {node.name: node for node in graph.nodes}
    effective_unreachable = _resolve_effective_unreachable(graph)
    effective_disconnected = _resolve_effective_disconnected(graph)

    findings.extend(_analyze_structure(inventory.workflow_name, graph, effective_unreachable, effective_disconnected))
    findings.extend(_analyze_credentials(inventory.nodes_with_credentials))
    findings.extend(_analyze_error_strategy(graph.nodes))
    findings.extend(_analyze_expression_references(graph.nodes, node_lookup))
    findings.extend(_analyze_http_redundancy(workflow, graph.nodes))
    findings.extend(_analyze_code_redundancy(workflow, graph.nodes))
    findings.extend(_analyze_prompt_redundancy(workflow, graph.nodes))
    findings.extend(_analyze_sql_queries(workflow, graph.nodes))
    findings.extend(_analyze_hardcoded_secrets(workflow, graph.nodes))
    findings.extend(_analyze_pinned_sample_data(workflow))
    findings.extend(_analyze_complexity(inventory, graph.nodes))

    counts_by_severity = dict(Counter(f.severity for f in findings))
    counts_by_category = dict(Counter(f.category for f in findings))
    scorecard = _build_scorecard(findings, inventory.total_nodes)
    return N8nWorkflowAnalysis(
        inventory=inventory,
        scorecard=scorecard,
        findings=findings,
        counts_by_severity=counts_by_severity,
        counts_by_category=counts_by_category,
        graph=graph if include_graph else None,
    )


def _analyze_structure(
    workflow_name: str,
    graph,
    effective_unreachable: List[str],
    effective_disconnected: List[str],
) -> List[N8nFinding]:
    findings: List[N8nFinding] = []
    if not graph.nodes:
        findings.append(
            N8nFinding(
                finding_id="structure-empty-workflow",
                category="structure",
                severity="critical",
                title="Workflow vacío",
                message=f"El workflow '{workflow_name}' no contiene nodos analizables.",
                recommendation="Exporta el workflow completo desde n8n e incluye la sección nodes/connections.",
            )
        )
        return findings

    if not graph.trigger_nodes:
        findings.append(
            N8nFinding(
                finding_id="structure-no-trigger",
                category="structure",
                severity="high",
                title="Sin nodo de entrada claro",
                message="No se detectó ningún trigger o webhook claro; el flujo podría no iniciar de forma controlada.",
                recommendation="Agrega un trigger explícito o verifica que el export JSON incluya el nodo inicial real.",
            )
        )

    if effective_unreachable:
        findings.append(
            N8nFinding(
                finding_id="structure-unreachable-nodes",
                category="structure",
                severity="high",
                title="Nodos no alcanzables desde el inicio",
                message="Hay nodos que no reciben flujo desde ningún trigger/webhook detectado.",
                node_names=effective_unreachable,
                evidence={"unreachable_nodes": effective_unreachable},
                recommendation="Revisa conexiones rotas, ramas huérfanas o nodos copiados que quedaron fuera del flujo principal.",
            )
        )

    if effective_disconnected:
        findings.append(
            N8nFinding(
                finding_id="structure-disconnected-nodes",
                category="maintainability",
                severity="medium",
                title="Nodos totalmente desconectados",
                message="Existen nodos sin entradas ni salidas; suelen ser restos de pruebas o lógica abandonada.",
                node_names=effective_disconnected,
                evidence={"disconnected_nodes": effective_disconnected},
                recommendation="Elimina estos nodos o reconéctalos si realmente forman parte del proceso esperado.",
            )
        )

    if len(graph.trigger_nodes) > 1:
        findings.append(
            N8nFinding(
                finding_id="structure-multiple-entrypoints",
                category="operations",
                severity="low",
                title="Múltiples puntos de entrada",
                message="El workflow tiene varios triggers/webhooks; esto no es incorrecto, pero aumenta la complejidad operativa.",
                node_names=graph.trigger_nodes,
                evidence={"trigger_nodes": graph.trigger_nodes},
                recommendation="Documenta qué ruta activa cada trigger y si comparten o no la misma lógica aguas abajo.",
            )
        )
    return findings


def _analyze_credentials(nodes_with_credentials: List[str]) -> List[N8nFinding]:
    if not nodes_with_credentials:
        return []
    return [
        N8nFinding(
            finding_id="security-credential-references",
            category="security",
            severity="info",
            title="El export incluye referencias a credenciales",
            message="Los exports de n8n suelen incluir nombres e IDs de credenciales. Eso no siempre es secreto, pero sí información sensible de operación.",
            node_names=nodes_with_credentials,
            evidence={"nodes_with_credentials": nodes_with_credentials},
            recommendation="Antes de compartir el JSON fuera del entorno controlado, anonimiza nombres de credenciales y revisa headers importados desde cURL.",
        )
    ]


def _analyze_error_strategy(nodes: Iterable[Any]) -> List[N8nFinding]:
    risky_nodes = [
        node.name
        for node in nodes
        if node.category in _RISKY_CATEGORIES
        and not node.disabled
        and not node.retry_on_fail
        and not node.continue_on_fail
        and not node.on_error
    ]
    if not risky_nodes:
        return []
    return [
        N8nFinding(
            finding_id="operations-missing-error-strategy",
            category="operations",
            severity="medium",
            title="Nodos críticos sin estrategia explícita de error",
            message="Hay nodos de integración/código/IA sin reintentos ni política de error explícita. Esto vuelve el flujo frágil ante fallos temporales.",
            node_names=sorted(risky_nodes)[:15],
            evidence={"nodes_without_error_strategy": sorted(risky_nodes)},
            recommendation="Define Retry On Fail, On Error o un flujo de error dedicado para los nodos más sensibles.",
        )
    ]


def _analyze_expression_references(nodes: Iterable[Any], node_lookup: Dict[str, Any]) -> List[N8nFinding]:
    findings: List[N8nFinding] = []
    missing_refs: dict[str, list[dict[str, str]]] = defaultdict(list)
    for node in nodes:
        raw_parameters = getattr(node, "raw_parameters", None)
        if raw_parameters is None:
            continue
        for path, value in iter_parameter_strings(raw_parameters):
            if not _looks_like_expression(value):
                continue
            for ref_name in _extract_node_references(value):
                if ref_name not in node_lookup:
                    missing_refs[node.name].append({"path": path, "reference": ref_name})
    if missing_refs:
        node_names = sorted(missing_refs.keys())
        findings.append(
            N8nFinding(
                finding_id="logic-broken-node-references",
                category="logic",
                severity="high",
                title="Expresiones con referencias a nodos inexistentes",
                message="Se detectaron expresiones que apuntan a nodos que no aparecen en el workflow exportado.",
                node_names=node_names,
                evidence={"missing_references": missing_refs},
                recommendation="Corrige las expresiones o verifica si el export quedó incompleto y faltan nodos/subworkflows.",
            )
        )
    return findings


def _analyze_http_redundancy(workflow: Dict[str, Any], nodes: Iterable[Any]) -> List[N8nFinding]:
    raw_nodes = workflow.get("nodes") or []
    by_name = {str(node.get("name")): node for node in raw_nodes if isinstance(node, dict)}
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for node in nodes:
        if node.category != "http":
            continue
        raw_node = by_name.get(node.name, {})
        params = raw_node.get("parameters") or {}
        method = str(params.get("method") or "GET").upper()
        url = _normalize_text(str(params.get("url") or ""))
        if not url:
            continue
        grouped[(method, url)].append(node.name)
    findings: List[N8nFinding] = []
    for (method, url), node_names in sorted(grouped.items()):
        if len(node_names) < 2:
            continue
        findings.append(
            N8nFinding(
                finding_id=f"redundancy-http-{abs(hash((method, url))) % 100000}",
                category="redundancy",
                severity="medium",
                title="Llamadas HTTP potencialmente duplicadas",
                message=f"Se encontraron varios HTTP Request con el mismo método y URL base: {method} {url}.",
                node_names=sorted(node_names),
                evidence={"method": method, "url": url, "nodes": sorted(node_names)},
                recommendation="Consolida llamadas repetidas, reutiliza subworkflows o centraliza la integración si la lógica es la misma.",
            )
        )
    return findings


def _analyze_code_redundancy(workflow: Dict[str, Any], nodes: Iterable[Any]) -> List[N8nFinding]:
    raw_nodes = workflow.get("nodes") or []
    by_name = {str(node.get("name")): node for node in raw_nodes if isinstance(node, dict)}
    grouped: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.category != "code":
            continue
        raw_node = by_name.get(node.name, {})
        params = raw_node.get("parameters") or {}
        code = _extract_code_block(params)
        normalized = _normalize_code(code)
        if normalized:
            grouped[normalized].append(node.name)
    findings: List[N8nFinding] = []
    for normalized_code, node_names in grouped.items():
        if len(node_names) < 2:
            continue
        findings.append(
            N8nFinding(
                finding_id=f"redundancy-code-{abs(hash(normalized_code)) % 100000}",
                category="redundancy",
                severity="medium",
                title="Code nodes con lógica duplicada",
                message="Hay múltiples Code/Function nodes con el mismo bloque lógico normalizado.",
                node_names=sorted(node_names),
                evidence={"nodes": sorted(node_names), "code_fingerprint": abs(hash(normalized_code)) % 100000},
                recommendation="Extrae esa lógica a un subworkflow o a un único Code node reutilizable para reducir deuda técnica.",
            )
        )
    return findings


def _analyze_prompt_redundancy(workflow: Dict[str, Any], nodes: Iterable[Any]) -> List[N8nFinding]:
    raw_nodes = workflow.get("nodes") or []
    by_name = {str(node.get("name")): node for node in raw_nodes if isinstance(node, dict)}
    grouped: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.category != "ai":
            continue
        raw_node = by_name.get(node.name, {})
        params = raw_node.get("parameters") or {}
        for path, value in iter_parameter_strings(params):
            if len(value.strip()) < 30:
                continue
            path_norm = path.lower().replace("_", "").replace(".", "")
            if not any(hint in path_norm for hint in _PROMPT_FIELD_HINTS):
                continue
            normalized = _normalize_text(value)
            if node.name not in grouped[normalized]:
                grouped[normalized].append(node.name)
    findings: List[N8nFinding] = []
    for prompt_text, node_names in grouped.items():
        if len(node_names) < 2:
            continue
        findings.append(
            N8nFinding(
                finding_id=f"redundancy-prompt-{abs(hash(prompt_text)) % 100000}",
                category="redundancy",
                severity="low",
                title="Prompts repetidos entre nodos IA",
                message="Hay nodos IA con prompts o instrucciones sustancialmente idénticas.",
                node_names=sorted(node_names),
                evidence={"nodes": sorted(node_names), "prompt_fingerprint": abs(hash(prompt_text)) % 100000},
                recommendation="Centraliza prompts compartidos o documenta por qué deben permanecer duplicados.",
            )
        )
    return findings


def _analyze_hardcoded_secrets(workflow: Dict[str, Any], nodes: Iterable[Any]) -> List[N8nFinding]:
    raw_nodes = workflow.get("nodes") or []
    by_name = {str(node.get("name")): node for node in raw_nodes if isinstance(node, dict)}
    exposures: dict[str, list[dict[str, str]]] = defaultdict(list)
    for node in nodes:
        raw_node = by_name.get(node.name, {})
        params = raw_node.get("parameters") or {}
        credentials = raw_node.get("credentials") or {}
        search_payload = {"parameters": params, "credentials": credentials}
        for path, value in iter_parameter_strings(search_payload):
            if _looks_like_expression(value):
                continue
            if _looks_like_secret(path, value):
                exposures[node.name].append({"path": path, "value_preview": _mask_value(value)})
    if not exposures:
        return []
    return [
        N8nFinding(
            finding_id="security-hardcoded-secrets",
            category="security",
            severity="critical",
            title="Posibles secretos hardcodeados",
            message="Se encontraron valores sensibles escritos directamente dentro del workflow exportado.",
            node_names=sorted(exposures.keys()),
            evidence={"exposures": exposures},
            recommendation="Mueve esos valores a credenciales/variables seguras de n8n y vuelve a exportar el flujo sin secretos embebidos.",
        )
    ]


def _analyze_sql_queries(workflow: Dict[str, Any], nodes: Iterable[Any]) -> List[N8nFinding]:
    raw_nodes = workflow.get("nodes") or []
    by_name = {str(node.get("name")): node for node in raw_nodes if isinstance(node, dict)}
    interpolation_hits: dict[str, dict[str, Any]] = {}
    multi_statement_hits: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if "postgres" not in str(getattr(node, "node_type", "")).lower():
            continue
        raw_node = by_name.get(node.name, {})
        params = raw_node.get("parameters") or {}
        query = str(params.get("query") or "")
        if not query:
            continue
        if "{{" in query and any(token in query for token in ("$json", "$('", '$("', "$items(")):
            interpolation_hits[node.name] = {"query_preview": _shorten_query(query)}
        statements = [part.strip() for part in query.split(";") if part.strip()]
        if len(statements) > 1:
            multi_statement_hits[node.name] = {"statement_count": len(statements), "query_preview": _shorten_query(query)}

    findings: List[N8nFinding] = []
    if interpolation_hits:
        findings.append(
            N8nFinding(
                finding_id="security-sql-string-interpolation",
                category="security",
                severity="high",
                title="Queries SQL con interpolación dinámica",
                message="Hay nodos Postgres que construyen queries con expresiones embebidas. Eso dificulta el control de errores y puede abrir riesgo de inyección o de queries mal formadas.",
                node_names=sorted(interpolation_hits.keys()),
                evidence={"sql_interpolation_nodes": interpolation_hits},
                recommendation="Usa parámetros preparados o variables estructuradas del nodo Postgres en lugar de concatenar/interpolar valores directamente en el SQL.",
            )
        )
    if multi_statement_hits:
        findings.append(
            N8nFinding(
                finding_id="operations-sql-multi-statement",
                category="operations",
                severity="medium",
                title="Queries SQL con múltiples sentencias",
                message="Se detectaron nodos Postgres que mezclan varias sentencias en una sola ejecución. Esto hace más frágil el manejo transaccional y de errores.",
                node_names=sorted(multi_statement_hits.keys()),
                evidence={"sql_multi_statement_nodes": multi_statement_hits},
                recommendation="Separa lecturas, borrados y actualizaciones en nodos distintos o usa transacciones explícitas con control de errores claro.",
            )
        )
    return findings


def _analyze_pinned_sample_data(workflow: Dict[str, Any]) -> List[N8nFinding]:
    pin_data = workflow.get("pinData") or {}
    if not isinstance(pin_data, dict) or not pin_data:
        return []

    secrets: dict[str, list[dict[str, str]]] = defaultdict(list)
    pii_hits: dict[str, list[str]] = defaultdict(list)
    for node_name, entries in pin_data.items():
        for path, value in iter_parameter_strings(entries, prefix=str(node_name)):
            if _looks_like_secret(path, value):
                secrets[str(node_name)].append({"path": path, "value_preview": _mask_value(value)})
            if _looks_like_phone(value):
                pii_hits[str(node_name)].append("telefono")
            if _looks_like_document(value):
                pii_hits[str(node_name)].append("documento")
            if _looks_like_address(value):
                pii_hits[str(node_name)].append("direccion")

    findings: List[N8nFinding] = []
    if secrets:
        findings.append(
            N8nFinding(
                finding_id="security-pindata-secrets",
                category="security",
                severity="critical",
                title="pinData contiene secretos o tokens",
                message="El archivo exportado incluye datos fijados (`pinData`) con valores sensibles. Esto puede exponer tokens, headers o credenciales de prueba.",
                node_names=sorted(secrets.keys()),
                evidence={"pin_data_secrets": secrets},
                recommendation="Limpia o elimina `pinData` antes de compartir el workflow. Nunca exportes ejemplos con tokens reales.",
            )
        )
    if pii_hits:
        normalized_hits = {node: sorted(set(labels)) for node, labels in pii_hits.items()}
        findings.append(
            N8nFinding(
                finding_id="security-pindata-pii",
                category="security",
                severity="high",
                title="pinData contiene datos personales de ejemplo",
                message="Se detectaron teléfonos, documentos o direcciones dentro de los datos fijados del workflow.",
                node_names=sorted(normalized_hits.keys()),
                evidence={"pin_data_pii": normalized_hits},
                recommendation="Borra o anonimiza los datos de prueba embebidos en `pinData` antes de reutilizar o compartir este flujo.",
            )
        )
    return findings


def _analyze_complexity(inventory, nodes: Iterable[Any]) -> List[N8nFinding]:
    findings: List[N8nFinding] = []
    if inventory.total_nodes >= 25:
        findings.append(
            N8nFinding(
                finding_id="maintainability-workflow-size",
                category="maintainability",
                severity="medium",
                title="Workflow con complejidad creciente",
                message=f"El flujo tiene {inventory.total_nodes} nodos. A partir de este tamaño suele aumentar el costo de mantenimiento y depuración.",
                evidence={"total_nodes": inventory.total_nodes},
                recommendation="Considera separar responsabilidades por subworkflows o dominios funcionales.",
            )
        )
    disabled = [node.name for node in nodes if node.disabled]
    if disabled:
        findings.append(
            N8nFinding(
                finding_id="maintainability-disabled-nodes",
                category="maintainability",
                severity="low",
                title="Nodos desactivados presentes en el flujo",
                message="Los nodos desactivados no siempre son un problema, pero suelen ser señal de pruebas parciales o lógica vieja acumulada.",
                node_names=sorted(disabled),
                evidence={"disabled_nodes": sorted(disabled)},
                recommendation="Documenta por qué siguen ahí o elimínalos si ya no forman parte del diseño actual.",
            )
        )
    return findings


def _build_scorecard(findings: List[N8nFinding], total_nodes: int) -> N8nScorecard:
    dimensions = {
        "workflow_integrity": 1.0,
        "maintainability": 1.0,
        "security_posture": 1.0,
        "operational_resilience": 1.0,
        "redundancy": 1.0,
    }
    penalties = {"critical": 0.20, "high": 0.12, "medium": 0.07, "low": 0.03, "info": 0.0}
    category_map = {
        "structure": ("workflow_integrity",),
        "logic": ("workflow_integrity", "operational_resilience"),
        "redundancy": ("redundancy", "maintainability"),
        "security": ("security_posture",),
        "operations": ("operational_resilience",),
        "maintainability": ("maintainability",),
    }
    for finding in findings:
        penalty = penalties[finding.severity]
        for dimension in category_map.get(finding.category, ()):
            dimensions[dimension] = max(0.0, dimensions[dimension] - penalty)

    if total_nodes >= 40:
        dimensions["maintainability"] = max(0.0, dimensions["maintainability"] - 0.05)

    overall = mean(dimensions.values())
    if any(f.severity in {"critical", "high"} for f in findings) or overall < 0.75:
        status = "fail"
    elif findings or overall < 0.90:
        status = "warning"
    else:
        status = "ok"

    return N8nScorecard(
        workflow_integrity=round(dimensions["workflow_integrity"], 4),
        maintainability=round(dimensions["maintainability"], 4),
        security_posture=round(dimensions["security_posture"], 4),
        operational_resilience=round(dimensions["operational_resilience"], 4),
        redundancy=round(dimensions["redundancy"], 4),
        overall=round(overall, 4),
        status=status,
    )


def _looks_like_expression(value: str) -> bool:
    text = str(value).strip()
    return text.startswith("=") or "$(" in text or "$node[" in text or "{{" in text


def _extract_node_references(value: str) -> List[str]:
    refs: list[str] = []
    for pattern in _NODE_REF_PATTERNS:
        refs.extend(pattern.findall(value))
    return sorted(set(refs))


def _extract_code_block(parameters: Dict[str, Any]) -> str:
    for candidate in ("jsCode", "functionCode", "code", "pythonCode"):
        value = parameters.get(candidate)
        if isinstance(value, str):
            return value
    return ""


def _normalize_code(code: str) -> str:
    if not code:
        return ""
    compact = re.sub(r"\s+", " ", code).strip()
    return compact if len(compact) >= 24 else ""


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _looks_like_secret(path: str, value: str) -> bool:
    path_norm = path.lower().replace("_", "").replace(".", "")
    if any(keyword in path_norm for keyword in _SECRET_KEYWORDS):
        return True
    return any(pattern.search(value or "") for pattern in _SECRET_VALUE_PATTERNS)


def _mask_value(value: str) -> str:
    text = value.strip()
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}***{text[-4:]}"


def _shorten_query(query: str, limit: int = 220) -> str:
    compact = re.sub(r"\s+", " ", query).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _resolve_effective_unreachable(graph) -> List[str]:
    node_lookup = {node.name: node for node in graph.nodes}
    reachable = {node.name for node in graph.nodes if node.name not in set(graph.unreachable_nodes)}
    changed = True
    while changed:
        changed = False
        for edge in graph.edges:
            if edge.channel.startswith("ai_") and edge.target in reachable and edge.source not in reachable:
                reachable.add(edge.source)
                changed = True
    result = [
        node.name
        for node in graph.nodes
        if node.name not in reachable and not _is_non_executable_note(node)
    ]
    return sorted(result)


def _resolve_effective_disconnected(graph) -> List[str]:
    return sorted([node.name for node in graph.nodes if node.name in graph.disconnected_nodes and not _is_non_executable_note(node)])


def _is_non_executable_note(node: Any) -> bool:
    return "stickynote" in str(getattr(node, "node_type", "")).lower()


def _looks_like_phone(value: str) -> bool:
    text = re.sub(r"\D", "", value or "")
    return len(text) >= 10


def _looks_like_document(value: str) -> bool:
    text = re.sub(r"\D", "", value or "")
    return 6 <= len(text) <= 12


def _looks_like_address(value: str) -> bool:
    text = _normalize_text(value)
    return any(token in text for token in ("calle ", "carrera ", "avenida ", "barrio ", "#"))
