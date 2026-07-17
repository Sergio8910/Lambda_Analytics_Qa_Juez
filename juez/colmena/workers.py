"""Obreras estaticas de proyecto para La Colmena."""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from .models import NormalizedFinding, ProjectInventory

_SECRET_RE = re.compile(
    r"(?i)\b[a-z_]{0,30}(?:api[_-]?key|apikey|secret|token|password|passwd|pwd|"
    r"private[_-]?key|credentials?|auth|clave|secreto|contrase[nñ]a|key)[a-z_]{0,20}"
    r"\s*(?::\s*[\w\[\], .]{1,20}\s*)?[:=]\s*['\"]?([A-Za-z0-9_\-./+=]{12,})"
)
# Secretos reales pasados como valor por defecto de una variable de entorno,
# ej. `os.environ.get("KEY", "sk-proj-...")` -- el regex generico de arriba
# NO los detecta porque el nombre sensible (KEY) esta en el primer argumento,
# no inmediatamente antes del valor real.
_ENV_DEFAULT_SECRET_RE = re.compile(
    r"(?i)(?:os\.(?:environ\.get|getenv)|getenv)\s*\(\s*['\"][A-Z0-9_]+['\"]\s*,\s*['\"]([A-Za-z0-9_\-./+=]{12,})['\"]"
)
_PRIVATE_URL_RE = re.compile(
    r"(169\.254\.169\.254|metadata\.google\.internal|localhost|127\.0\.0\.1|0\.0\.0\.0|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})",
    re.I,
)
_HTTP_CALL_RE = re.compile(r"\b(requests|httpx)\.(get|post|put|patch|delete|request)\((?P<args>[^)]*)\)")

# Jailbreak / prompt-injection: antes era substring literal de dos frases
# ("ignora instrucciones" / "ignore previous instructions"), evadible con
# cualquier variante. Cada patron cubre una FAMILIA de ataque (ES + EN):
# desobedecer instrucciones, reasignar rol, activar "modo sin restricciones",
# o exfiltrar el prompt/sistema.
_PROMPT_INJECTION_RES = (
    re.compile(r"(?i)\b(ignor[ae]|olvid[ae]|descart[ae])\b.{0,40}\b(instruc\w*|regl\w*|orden\w*|prompt|context\w*)"),
    re.compile(r"(?i)\b(ignore|forget|disregard|override|bypass)\b.{0,40}\b(instruction|rule|prompt|context|previous|above|prior)"),
    re.compile(r"(?i)\b(ahora eres|a partir de ahora eres|actua como si|finge que eres|haz de cuenta que eres)\b"),
    re.compile(r"(?i)\b(you are now|from now on you are|act as|pretend (to be|you are)|roleplay as)\b"),
    re.compile(r"(?i)\b(modo (desarrollador|dios|sin restricc\w*|libre)|developer mode|jailbreak|do anything now|\bDAN\b)"),
    re.compile(r"(?i)\b(sin (restricc\w*|filtro|limit\w*|censura)|no tienes (restricc\w*|limit\w*)|without (restrictions?|filter|limits?|guardrails?))"),
    re.compile(r"(?i)\b(revel[ae]|muestr[ae]|repite|dime)\b.{0,40}\b(system prompt|prompt del sistema|instrucc\w* internas?|tus instrucc\w*|reglas internas?)"),
    re.compile(r"(?i)\b(reveal|show|print|repeat|leak)\b.{0,40}\b(system prompt|your (instructions?|prompt|rules)|initial prompt|hidden (instructions?|rules))"),
)
# Contexto DEFENSIVO: una linea que le dice al agente que RECHACE la inyeccion
# ("rechaza jailbreaks", "no reveles instrucciones internas", "si te piden
# ignorar instrucciones, rechazala") contiene el vocabulario del ataque pero
# es exactamente lo contrario a una vulnerabilidad. Sin este guard, los propios
# guardrails que agrega el self-heal se marcarian como inyeccion.
_PROMPT_INJECTION_DEFENSIVE_RE = re.compile(
    r"(?i)\b(rechaz\w*|declin\w*|reject|refuse|deny|"
    r"no (revel\w*|obedez\w*|ignor\w*|sigas|compart\w*|acat\w*)|"
    r"must not|do not|don't|never (reveal|obey|follow|share|ignore))\b"
)


class FindingBuilder:
    def __init__(self) -> None:
        self._seq = 0

    def make(
        self,
        *,
        severity: str,
        category: str,
        title: str,
        description: str,
        source: str,
        file: str | None = None,
        line: int | None = None,
        evidence: str = "",
        impact: str = "",
        recommendation: str = "",
        auto_fix_available: bool = False,
    ) -> NormalizedFinding:
        self._seq += 1
        prefix = {
            "security": "SEC",
            "api": "API",
            "workflow": "WF",
            "agent": "AGT",
            "prompt": "PRM",
            "architecture": "ARC",
            "documentation": "DOC",
            "deployment": "DEP",
            "testing": "TST",
            "business_rule": "RN",
        }.get(category, "COL")
        return NormalizedFinding(
            id=f"{prefix}-{self._seq:03d}",
            severity=severity,  # type: ignore[arg-type]
            category=category,  # type: ignore[arg-type]
            title=title,
            description=description,
            file=file,
            line=line,
            evidence=evidence[:500],
            impact=impact,
            recommendation=recommendation,
            auto_fix_available=auto_fix_available,
            source=source,
        )


def evaluate_project_workers(root: Path, inventory: ProjectInventory) -> list[NormalizedFinding]:
    builder = FindingBuilder()
    findings: list[NormalizedFinding] = []
    findings.extend(SecurityWorker(root, inventory, builder).run())
    if inventory.detected_assets.get("apis", 0) or any(f in inventory.frameworks for f in ("fastapi", "flask", "django")):
        findings.extend(ApiWorker(root, inventory, builder).run())
    if inventory.detected_assets.get("workflows", 0):
        findings.extend(WorkflowWorker(root, inventory, builder).run())
    if inventory.detected_assets.get("agents", 0) or inventory.detected_assets.get("prompts", 0):
        findings.extend(AgentPromptWorker(root, inventory, builder).run())
    findings.extend(ArchitectureWorker(root, inventory, builder).run())
    findings.extend(DocumentationWorker(root, inventory, builder).run())
    findings.extend(DeploymentWorker(root, inventory, builder).run())
    findings.extend(TestingWorker(root, inventory, builder).run())
    # Archivos omitidos por tamano: un componente no analizado no debe pasar
    # como "sin hallazgos" -- se reporta como medium para que sea visible.
    for asset in inventory.assets:
        if asset.kind == "skipped_large_file":
            findings.append(builder.make(
                severity="medium",
                category="maintainability",
                title="Archivo no analizado por tamano (>2MB)",
                description=(
                    "El archivo supera el limite de 2MB y NO fue analizado. Si es un flujo n8n "
                    "o codigo del proyecto, su contenido quedo fuera de la evaluacion."
                ),
                file=asset.path,
                impact="Un componente sin analizar puede esconder problemas que el reporte no refleja.",
                recommendation="Reducir el tamano (quitar datos de ejemplo embebidos) o analizarlo por separado.",
                source="coverage_worker",
            ))
    return sorted(findings, key=lambda f: (_severity_rank(f.severity), f.category, f.id))


class BaseWorker:
    source = "base_worker"

    def __init__(self, root: Path, inventory: ProjectInventory, builder: FindingBuilder) -> None:
        self.root = root
        self.inventory = inventory
        self.f = builder

    def run(self) -> list[NormalizedFinding]:
        return []

    def text_files(self, suffixes: set[str] | None = None) -> Iterable[tuple[str, str]]:
        for asset in self.inventory.assets:
            path = self.root / asset.path
            if suffixes and path.suffix.lower() not in suffixes and path.name.lower() not in suffixes:
                continue
            try:
                yield asset.path, path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue


def _valor_secreto_plausible(line: str, match: re.Match) -> bool:
    """Filtra falsos positivos del regex generico de secretos: el valor
    capturado debe verse como un secreto real (contiene al menos un digito)
    y no ser una llamada a funcion/atributo (ej. `api_key = load_from_vault()`
    o `api_key = os.environ.get(...)`, donde el regex capturaria el nombre de
    la funcion/metodo, no un valor literal)."""
    valor = match.group(1)
    if not any(c.isdigit() for c in valor):
        return False
    fin = match.end(1)
    return not (fin < len(line) and line[fin] == "(")


class SecurityWorker(BaseWorker):
    source = "security_worker"

    def run(self) -> list[NormalizedFinding]:
        out: list[NormalizedFinding] = []
        # Incluye .txt: los prompts de agente (el asset mas evaluado por el
        # Juez) se materializan como .txt, y un secreto pegado dentro del
        # system prompt (comun cuando un agente "explica" como llamar una
        # API) antes quedaba invisible para este worker.
        for rel, text in self.text_files({".py", ".json", ".yml", ".yaml", ".env", ".toml", ".md", ".txt"}):
            es_ejemplo = "example" in rel.lower()
            for line_no, line in enumerate(text.splitlines(), start=1):
                m = _SECRET_RE.search(line)
                if m and _valor_secreto_plausible(line, m) and not es_ejemplo:
                    out.append(self.f.make(
                        severity="critical",
                        category="security",
                        title="Posible secreto hardcodeado",
                        description="Se detecto una variable sensible con valor embebido en el proyecto.",
                        file=rel,
                        line=line_no,
                        evidence=line.strip(),
                        impact="Puede exponer credenciales reales o tokens de acceso.",
                        recommendation="Mover secretos a variables de entorno o gestor de secretos y rotarlos si son reales.",
                        source=self.source,
                    ))
                m_env = _ENV_DEFAULT_SECRET_RE.search(line)
                if m_env and not es_ejemplo:
                    out.append(self.f.make(
                        severity="critical",
                        category="security",
                        title="Secreto real como valor por defecto de una variable de entorno",
                        description=(
                            "Se detecto un valor con forma de secreto real usado como fallback de "
                            "os.environ.get()/getenv() -- si la variable de entorno no esta seteada, "
                            "el codigo usa este valor embebido en vez de fallar."
                        ),
                        file=rel,
                        line=line_no,
                        evidence=line.strip(),
                        impact="El fallback puede ser una credencial real que queda expuesta en el repo.",
                        recommendation="Eliminar el valor por defecto (que falle si falta la env var) y rotar el secreto si es real.",
                        source=self.source,
                    ))
                if _PRIVATE_URL_RE.search(line):
                    out.append(self.f.make(
                        severity="critical",
                        category="security",
                        title="Posible SSRF o acceso a red interna",
                        description="El codigo o configuracion referencia hosts internos/metadata.",
                        file=rel,
                        line=line_no,
                        evidence=line.strip(),
                        impact="Un atacante podria forzar acceso a recursos internos si esta URL depende de input.",
                        recommendation="Validar dominios permitidos y bloquear IPs privadas/metadata.",
                        auto_fix_available=True,
                        source=self.source,
                    ))
                if "allow_origins=[\"*\"]" in line or "allow_origins=['*']" in line:
                    out.append(self.f.make(
                        severity="high",
                        category="security",
                        title="CORS permisivo",
                        description="La API permite cualquier origen.",
                        file=rel,
                        line=line_no,
                        evidence=line.strip(),
                        impact="Puede facilitar abuso desde navegadores si hay endpoints sensibles.",
                        recommendation="Configurar una allowlist de origenes por ambiente.",
                        source=self.source,
                    ))
                if re.search(r"\b(eval|exec)\(", line):
                    out.append(self.f.make(
                        severity="high",
                        category="security",
                        title="Ejecucion dinamica peligrosa",
                        description="Se detecto uso de eval/exec.",
                        file=rel,
                        line=line_no,
                        evidence=line.strip(),
                        impact="Puede permitir ejecucion arbitraria si llega input externo.",
                        recommendation="Reemplazar por parseo estructurado o dispatch explicito.",
                        source=self.source,
                    ))
                if "subprocess." in line and "shell=True" in line:
                    out.append(self.f.make(
                        severity="high",
                        category="security",
                        title="Subprocess con shell=True",
                        description="Se detecto ejecucion de shell con riesgo de inyeccion.",
                        file=rel,
                        line=line_no,
                        evidence=line.strip(),
                        impact="Input no confiable podria ejecutar comandos arbitrarios.",
                        recommendation="Usar lista de argumentos y validar entradas.",
                        source=self.source,
                    ))
        if any(p.endswith(".env") for p in self.inventory.env_files):
            out.append(self.f.make(
                severity="medium",
                category="security",
                title=".env presente en el proyecto",
                description="Existe un archivo .env dentro del arbol evaluado.",
                file=", ".join(self.inventory.env_files),
                impact="Puede contener secretos si se comparte o despliega por error.",
                recommendation="Asegurar .gitignore, usar .env.example sin valores reales y gestor de secretos.",
                source=self.source,
            ))
        return out


class ApiWorker(BaseWorker):
    source = "api_worker"

    def run(self) -> list[NormalizedFinding]:
        out: list[NormalizedFinding] = []
        auth_tokens = ("Depends(", "Security(", "HTTPBearer", "APIKey", "OAuth2")
        for rel, text in self.text_files({".py"}):
            if re.search(r"@(app|router)\.(get|post|put|patch|delete)\(", text) and not any(t in text for t in auth_tokens):
                out.append(self.f.make(
                    severity="high",
                    category="api",
                    title="Endpoints sin autenticacion evidente",
                    description="Se detectaron rutas API sin dependencia de autenticacion en el archivo.",
                    file=rel,
                    impact="Endpoints internos podrian quedar expuestos.",
                    recommendation="Agregar autenticacion/authorization o documentar por que son publicos.",
                    source=self.source,
                ))
            for match in _HTTP_CALL_RE.finditer(text):
                args = match.group("args")
                if "timeout=" not in args:
                    line = text[: match.start()].count("\n") + 1
                    out.append(self.f.make(
                        severity="medium",
                        category="api",
                        title="Llamada HTTP sin timeout",
                        description="Una llamada externa no define timeout.",
                        file=rel,
                        line=line,
                        evidence=match.group(0),
                        impact="La API puede colgarse ante proveedores lentos.",
                        recommendation="Agregar timeout explicito y manejo de errores.",
                        auto_fix_available=True,
                        source=self.source,
                    ))
            if "raise HTTPException(status_code=500, detail=str(exc))" in text:
                out.append(self.f.make(
                    severity="medium",
                    category="api",
                    title="Error interno expuesto al cliente",
                    description="Se retorna str(exc) directamente en un HTTP 500.",
                    file=rel,
                    evidence="detail=str(exc)",
                    impact="Puede filtrar detalles internos.",
                    recommendation="Loguear el error y devolver un mensaje generico con correlation id.",
                    source=self.source,
                ))
        return out


class WorkflowWorker(BaseWorker):
    source = "workflow_worker"

    def run(self) -> list[NormalizedFinding]:
        out: list[NormalizedFinding] = []
        workflow_assets = [a for a in self.inventory.assets if a.kind == "n8n_workflow"]
        for asset in workflow_assets:
            path = self.root / asset.path
            try:
                wf = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                continue
            try:
                from juez.evaluation.n8n import analyze_workflow

                analysis = analyze_workflow(wf)
                for finding in analysis.findings:
                    out.append(self.f.make(
                        severity=finding.severity,
                        category="workflow",
                        title=finding.title,
                        description=finding.message,
                        file=asset.path,
                        evidence=", ".join(finding.node_names),
                        recommendation=finding.recommendation,
                        source=self.source,
                    ))
            except Exception as exc:
                out.append(self.f.make(
                    severity="info",
                    category="workflow",
                    title="No se pudo ejecutar analisis n8n",
                    description=f"{type(exc).__name__}: {exc}",
                    file=asset.path,
                    recommendation="Revisar si el JSON es un export n8n completo.",
                    source=self.source,
                ))
        return out


class AgentPromptWorker(BaseWorker):
    source = "agent_prompt_worker"

    def run(self) -> list[NormalizedFinding]:
        out: list[NormalizedFinding] = []
        for rel, text in self.text_files({".py", ".md", ".txt", ".json"}):
            lower = text.lower()
            if not ("prompt" in lower or "system" in lower or "agente" in lower):
                continue
            # Deteccion POR LINEA (no de todo el archivo): permite reportar el
            # fragmento exacto para que el fixer pueda eliminarlo, no solo
            # agregar guardrails encima (causa raiz vs sintoma).
            for line_no, raw_line in enumerate(text.splitlines(), start=1):
                if any(rx.search(raw_line) for rx in _PROMPT_INJECTION_RES) and not _PROMPT_INJECTION_DEFENSIVE_RE.search(raw_line):
                    out.append(self.f.make(
                        severity="high",
                        category="prompt",
                        title="Prompt vulnerable a inyeccion",
                        description="El prompt o documentacion contiene patrones de jailbreak sin mitigacion clara.",
                        file=rel,
                        line=line_no,
                        evidence=raw_line.strip(),
                        impact="El agente puede obedecer instrucciones maliciosas o fuera de alcance.",
                        recommendation="Eliminar la instruccion de jailbreak especifica y agregar guardrails.",
                        auto_fix_available=True,
                        source=self.source,
                    ))
            if "api_key" in lower or "token" in lower or "password" in lower:
                out.append(self.f.make(
                    severity="critical",
                    category="prompt",
                    title="Secreto referenciado en prompt",
                    description="El prompt parece contener o mencionar credenciales sensibles.",
                    file=rel,
                    impact="El agente podria filtrar secretos por conversacion.",
                    recommendation="Eliminar secretos de prompts y usar runtime secret management.",
                    source=self.source,
                ))
        return out


class ArchitectureWorker(BaseWorker):
    source = "architecture_worker"

    def run(self) -> list[NormalizedFinding]:
        out: list[NormalizedFinding] = []
        py_files = [a for a in self.inventory.assets if a.kind == "python_file"]
        for asset in py_files:
            path = self.root / asset.path
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                continue
            if len(lines) > 900:
                out.append(self.f.make(
                    severity="medium",
                    category="architecture",
                    title="Archivo Python muy grande",
                    description="El archivo concentra demasiada logica.",
                    file=asset.path,
                    impact="Dificulta mantenimiento, pruebas y revision de riesgos.",
                    recommendation="Separar responsabilidades en modulos pequenos.",
                    source=self.source,
                ))
        if self.inventory.detected_assets.get("python_file", 0) > 0 and not (self.root / "src").exists() and not (self.root / "juez").exists():
            out.append(self.f.make(
                severity="low",
                category="architecture",
                title="Estructura Python poco explicita",
                description="No se detecto carpeta de paquete/src clara.",
                impact="Puede complicar imports, empaquetado y despliegue.",
                recommendation="Mantener un paquete principal o layout src documentado.",
                source=self.source,
            ))
        return out


class DocumentationWorker(BaseWorker):
    source = "documentation_worker"

    def run(self) -> list[NormalizedFinding]:
        readme = _find_root_file(self.root, {"readme.md", "readme.rst"})
        if readme is None:
            return [self.f.make(
                severity="medium",
                category="documentation",
                title="README faltante",
                description="No se encontro README en la raiz del proyecto.",
                impact="Dificulta instalacion, operacion y transferencia.",
                recommendation="Agregar README con instalacion, ejecucion, variables de entorno, tests y limites.",
                auto_fix_available=True,
                source=self.source,
            )]
        text = readme.read_text(encoding="utf-8", errors="ignore").lower()
        missing = [word for word in ("instal", "ejec", "test", "env") if word not in text]
        if missing:
            return [self.f.make(
                severity="low",
                category="documentation",
                title="README incompleto",
                description="El README existe, pero no cubre instalacion/ejecucion/tests/env de forma suficiente.",
                file=readme.name,
                recommendation="Completar secciones operativas minimas.",
                source=self.source,
            )]
        return []


class DeploymentWorker(BaseWorker):
    source = "deployment_worker"

    def run(self) -> list[NormalizedFinding]:
        out: list[NormalizedFinding] = []
        has_docker = self.inventory.detected_assets.get("docker_files", 0) > 0
        if self.inventory.detected_assets.get("apis", 0) and not has_docker:
            out.append(self.f.make(
                severity="medium",
                category="deployment",
                title="API sin Dockerfile/docker-compose",
                description="Se detecto API pero no artefacto Docker.",
                impact="Puede dificultar despliegue reproducible.",
                recommendation="Agregar Dockerfile o documentar runtime administrado equivalente.",
                auto_fix_available=True,
                source=self.source,
            ))
        for rel, text in self.text_files({".yml", ".yaml"}):
            if "docker" in rel.lower() or "compose" in rel.lower():
                if "healthcheck" not in text.lower():
                    out.append(self.f.make(
                        severity="low",
                        category="deployment",
                        title="Compose sin healthcheck",
                        description="El compose no declara healthcheck.",
                        file=rel,
                        recommendation="Agregar healthcheck para servicios criticos.",
                        source=self.source,
                    ))
        if self.inventory.env_files and not any(p.endswith(".env.example") for p in self.inventory.env_files):
            out.append(self.f.make(
                severity="medium",
                category="deployment",
                title=".env.example faltante",
                description="Hay archivos .env pero no plantilla .env.example.",
                impact="Riesgo de compartir secretos reales o fallar configuracion por ambiente.",
                recommendation="Crear .env.example con nombres de variables y valores dummy.",
                auto_fix_available=True,
                source=self.source,
            ))
        return out


class TestingWorker(BaseWorker):
    source = "testing_worker"

    def run(self) -> list[NormalizedFinding]:
        if self.inventory.detected_assets.get("tests", 0) == 0:
            return [self.f.make(
                severity="medium",
                category="testing",
                title="No se detectaron tests",
                description="El proyecto no contiene tests reconocibles.",
                impact="No hay red de seguridad para cambios, fixes o despliegues.",
                recommendation="Agregar tests unitarios/smoke sin depender de produccion.",
                auto_fix_available=True,
                source=self.source,
            )]
        return []


def _severity_rank(value: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(value, 9)


def _find_root_file(root: Path, names: set[str]) -> Path | None:
    for child in root.iterdir():
        if child.is_file() and child.name.lower() in names:
            return child
    return None
