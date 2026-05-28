from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Dict, Iterable, List, Tuple

from .models import N8nEdge, N8nGraph, N8nNode, N8nWorkflowInventory


def parse_workflow(workflow: Dict[str, Any]) -> tuple[N8nWorkflowInventory, N8nGraph]:
    raw_nodes = workflow.get("nodes") or []
    raw_connections = workflow.get("connections") or {}

    nodes: List[N8nNode] = []
    node_names: set[str] = set()
    for index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, dict):
            continue
        name = str(raw_node.get("name") or f"node_{index}")
        node_type = str(raw_node.get("type") or "unknown")
        controls = _extract_controls(raw_node)
        credentials = raw_node.get("credentials") or {}
        credential_keys = list(credentials.keys()) if isinstance(credentials, dict) else []
        node = N8nNode(
            node_id=str(raw_node.get("id") or name),
            name=name,
            node_type=node_type,
            category=_classify_node(node_type),
            raw_parameters=(raw_node.get("parameters") or {}),
            disabled=bool(raw_node.get("disabled", False)),
            has_credentials=bool(credential_keys),
            credential_keys=credential_keys,
            on_error=controls["on_error"],
            retry_on_fail=controls["retry_on_fail"],
            continue_on_fail=controls["continue_on_fail"],
        )
        nodes.append(node)
        node_names.add(name)

    edges = _parse_edges(raw_connections)

    incoming: dict[str, int] = defaultdict(int)
    outgoing: dict[str, int] = defaultdict(int)
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[edge.source] += 1
        incoming[edge.target] += 1
        adjacency[edge.source].append(edge.target)

    for node in nodes:
        node.incoming_edges = incoming.get(node.name, 0)
        node.outgoing_edges = outgoing.get(node.name, 0)

    trigger_nodes = [n.name for n in nodes if n.category in {"trigger", "webhook"}]
    if not trigger_nodes:
        trigger_nodes = [n.name for n in nodes if n.incoming_edges == 0]

    reachable = _bfs(trigger_nodes, adjacency)
    unreachable_nodes = sorted([n.name for n in nodes if n.name not in reachable]) if trigger_nodes else []
    disconnected_nodes = sorted(
        [n.name for n in nodes if n.incoming_edges == 0 and n.outgoing_edges == 0 and n.category not in {"trigger", "webhook"}]
    )

    inventory = N8nWorkflowInventory(
        workflow_name=str(workflow.get("name") or "workflow_sin_nombre"),
        workflow_id=str(workflow.get("id")) if workflow.get("id") is not None else None,
        active=workflow.get("active"),
        total_nodes=len(nodes),
        total_edges=len(edges),
        trigger_nodes=sorted(trigger_nodes),
        webhook_nodes=sorted([n.name for n in nodes if n.category == "webhook"]),
        http_nodes=sorted([n.name for n in nodes if n.category == "http"]),
        ai_nodes=sorted([n.name for n in nodes if n.category == "ai"]),
        code_nodes=sorted([n.name for n in nodes if n.category == "code"]),
        nodes_with_credentials=sorted([n.name for n in nodes if n.has_credentials]),
    )
    graph = N8nGraph(
        nodes=nodes,
        edges=edges,
        trigger_nodes=sorted(trigger_nodes),
        unreachable_nodes=unreachable_nodes,
        disconnected_nodes=disconnected_nodes,
    )
    return inventory, graph


def iter_parameter_strings(value: Any, prefix: str = "") -> Iterable[Tuple[str, str]]:
    if isinstance(value, str):
        yield prefix or "value", value
        return
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from iter_parameter_strings(child, child_prefix)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            yield from iter_parameter_strings(child, child_prefix)


def _parse_edges(raw_connections: Dict[str, Any]) -> List[N8nEdge]:
    edges: List[N8nEdge] = []
    if not isinstance(raw_connections, dict):
        return edges

    for source_name, channels in raw_connections.items():
        if not isinstance(channels, dict):
            continue
        for channel_name, branch_groups in channels.items():
            if not isinstance(branch_groups, list):
                continue
            for output_index, branch in enumerate(branch_groups):
                if not isinstance(branch, list):
                    continue
                for raw_edge in branch:
                    if not isinstance(raw_edge, dict):
                        continue
                    target_name = str(raw_edge.get("node") or "").strip()
                    if not target_name:
                        continue
                    edges.append(
                        N8nEdge(
                            source=str(source_name),
                            target=target_name,
                            channel=str(channel_name or "main"),
                            output_index=output_index,
                            target_input_type=(
                                str(raw_edge.get("type")) if raw_edge.get("type") is not None else None
                            ),
                        )
                    )
    return edges


def _classify_node(node_type: str) -> str:
    node_type_norm = node_type.lower()
    if "webhook" in node_type_norm and "respond" not in node_type_norm:
        return "webhook"
    if any(
        token in node_type_norm
        for token in (
            "trigger",
            "manualtrigger",
            "scheduletrigger",
            "cron",
            "interval",
            "errortrigger",
        )
    ):
        return "trigger"
    if "httprequest" in node_type_norm:
        return "http"
    if any(token in node_type_norm for token in ("executeworkflow", "subworkflow")):
        return "subworkflow"
    if any(token in node_type_norm for token in ("code", "functionitem", "function", "executecommand")):
        return "code"
    if any(token in node_type_norm for token in ("if", "switch", "merge", "router")):
        return "logic"
    if any(
        token in node_type_norm
        for token in ("langchain", "openai", "anthropic", "agent", "assistant", "chatmodel", "lmchat")
    ):
        return "ai"
    if any(token in node_type_norm for token in ("set", "editfields", "aggregate", "itemlists", "filter")):
        return "data"
    return "other"


def _extract_controls(node: Dict[str, Any]) -> Dict[str, Any]:
    node_settings = node.get("settings") or {}
    parameters = node.get("parameters") or {}
    on_error = node.get("onError") or node_settings.get("onError") or parameters.get("onError")
    retry_on_fail = bool(
        node.get("retryOnFail")
        or node_settings.get("retryOnFail")
        or parameters.get("retryOnFail")
    )
    continue_on_fail = bool(
        node.get("continueOnFail")
        or node_settings.get("continueOnFail")
        or parameters.get("continueOnFail")
    )
    return {
        "on_error": str(on_error) if on_error is not None else None,
        "retry_on_fail": retry_on_fail,
        "continue_on_fail": continue_on_fail,
    }


def _bfs(start_nodes: List[str], adjacency: Dict[str, List[str]]) -> set[str]:
    visited: set[str] = set()
    queue = deque(start_nodes)
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for nxt in adjacency.get(current, []):
            if nxt not in visited:
                queue.append(nxt)
    return visited
