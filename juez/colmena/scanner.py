"""Scanner estatico de proyectos para La Colmena."""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from .models import ProjectAsset, ProjectInventory

_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "outputs",
    "dist",
    "build",
}
_PROMPT_HINT = re.compile(r"(system prompt|prompt_base|instrucciones|jailbreak|agente ia|llm)", re.I)
_HTTP_HINT = re.compile(r"\b(requests|httpx|aiohttp|fetch|axios)\b|https?://", re.I)


def scan_project(root: Path | str) -> ProjectInventory:
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"El proyecto no existe: {root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"El proyecto debe ser una carpeta: {root_path}")

    assets: list[ProjectAsset] = []
    frameworks: set[str] = set()
    integrations: set[str] = set()
    entrypoints: set[str] = set()
    env_files: list[str] = []

    for path in _iter_files(root_path):
        rel = _rel(path, root_path)
        lower_name = path.name.lower()
        suffix = path.suffix.lower()
        text = _read_text(path)

        is_test_file = "tests" in path.relative_to(root_path).parts or path.name.startswith("test_") or path.name.endswith("_test.py")

        if is_test_file:
            assets.append(ProjectAsset(kind="test_file", path=rel, name=path.name))

        if suffix == ".py":
            assets.append(ProjectAsset(kind="python_file", path=rel, name=path.name))
            _detect_python(text, rel, frameworks, integrations, entrypoints, assets)
        elif lower_name == "package.json":
            assets.append(ProjectAsset(kind="package_json", path=rel, name=path.name))
            _detect_package_json(text, frameworks, integrations)
        elif suffix in {".md", ".rst", ".txt"}:
            kind = "documentation"
            if _PROMPT_HINT.search(text or ""):
                kind = "prompt_doc"
            assets.append(ProjectAsset(kind=kind, path=rel, name=path.name))
        elif suffix == ".json":
            _detect_json(path, text, rel, assets, integrations)
        elif lower_name in {"dockerfile", "dockerfile.prod", "dockerfile.dev"}:
            assets.append(ProjectAsset(kind="dockerfile", path=rel, name=path.name))
        elif lower_name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
            assets.append(ProjectAsset(kind="docker_compose", path=rel, name=path.name))
        elif lower_name in {"requirements.txt", "pyproject.toml", "poetry.lock", "pdm.lock"}:
            assets.append(ProjectAsset(kind="python_dependency_file", path=rel, name=path.name))
        elif lower_name in {".env", ".env.example", ".env.local", ".env.dev", ".env.prod"}:
            assets.append(ProjectAsset(kind="env_file", path=rel, name=path.name))
            env_files.append(rel)
        elif lower_name in {"makefile", "justfile"} or path.parent.name.lower() == "scripts":
            assets.append(ProjectAsset(kind="script", path=rel, name=path.name))

        if text and _HTTP_HINT.search(text):
            integrations.add("external_http")

    counts = Counter(asset.kind for asset in assets)
    counts["agents"] = _count_agent_assets(assets)
    counts["prompts"] = sum(1 for a in assets if a.kind in {"prompt", "prompt_doc"})
    counts["apis"] = sum(1 for a in assets if a.kind == "api_endpoint")
    counts["workflows"] = sum(1 for a in assets if a.kind == "n8n_workflow")
    counts["env_files"] = len(env_files)
    counts["docker_files"] = counts.get("dockerfile", 0) + counts.get("docker_compose", 0)
    counts["tests"] = sum(1 for a in assets if a.kind == "test_file")
    counts["docs"] = sum(1 for a in assets if a.kind == "documentation")

    return ProjectInventory(
        root_path=str(root_path),
        detected_assets=dict(sorted(counts.items())),
        assets=assets,
        frameworks=sorted(frameworks),
        integrations=sorted(integrations),
        env_files=env_files,
        entrypoints=sorted(entrypoints),
    )


def _iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in _IGNORED_DIRS for part in path.relative_to(root).parts):
            continue
        if path.stat().st_size > 2_000_000:
            continue
        yield path


def _detect_python(
    text: str,
    rel: str,
    frameworks: set[str],
    integrations: set[str],
    entrypoints: set[str],
    assets: list[ProjectAsset],
) -> None:
    if "FastAPI(" in text or "from fastapi" in text or "import fastapi" in text:
        frameworks.add("fastapi")
    if "Flask(" in text or "from flask" in text:
        frameworks.add("flask")
    if "django" in text.lower():
        frameworks.add("django")
    if "mcp" in text.lower() and ("Server(" in text or "tool(" in text):
        frameworks.add("mcp")
    if re.search(r"@(app|router)\.(get|post|put|patch|delete)\(", text):
        endpoints = re.findall(r"@(app|router)\.(get|post|put|patch|delete)\(([^)]*)\)", text)
        for _, method, raw in endpoints:
            route = raw.split(",")[0].strip().strip("\"'")
            entrypoints.add(f"{method.upper()} {route}")
            assets.append(ProjectAsset(kind="api_endpoint", path=rel, name=method.upper()))
    if "requests." in text or "httpx." in text or "aiohttp" in text:
        integrations.add("external_http")
    if _PROMPT_HINT.search(text):
        assets.append(ProjectAsset(kind="prompt", path=rel, name=Path(rel).name))
    if re.search(r"\b(agent|Agent|assistant|tools)\b", text) and _PROMPT_HINT.search(text):
        assets.append(ProjectAsset(kind="agent", path=rel, name=Path(rel).name))


def _detect_package_json(text: str, frameworks: set[str], integrations: set[str]) -> None:
    try:
        data = json.loads(text)
    except Exception:
        return
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    if any(k in deps for k in ("react", "next", "vite", "vue", "svelte")):
        frameworks.add("frontend")
    if any(k in deps for k in ("express", "fastify", "koa")):
        frameworks.add("node_api")
    if any(k in deps for k in ("axios", "node-fetch")):
        integrations.add("external_http")


def _detect_json(path: Path, text: str, rel: str, assets: list[ProjectAsset], integrations: set[str]) -> None:
    try:
        data = json.loads(text)
    except Exception:
        assets.append(ProjectAsset(kind="json_config", path=rel, name=path.name))
        return
    if isinstance(data, dict) and "nodes" in data and "connections" in data:
        assets.append(ProjectAsset(kind="n8n_workflow", path=rel, name=str(data.get("name") or path.stem)))
        integrations.add("n8n")
    elif isinstance(data, dict) and "mcpServers" in data:
        assets.append(ProjectAsset(kind="mcp_config", path=rel, name=path.name))
    else:
        assets.append(ProjectAsset(kind="json_config", path=rel, name=path.name))


def _count_agent_assets(assets: list[ProjectAsset]) -> int:
    explicit = sum(1 for a in assets if a.kind == "agent")
    if explicit:
        return explicit
    return sum(1 for a in assets if a.kind == "prompt")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")
