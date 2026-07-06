"""Obrera de reglas de negocio + verificacion funcional (Parte 1).

Extrae reglas de negocio de tres fuentes, con nivel de confianza segun origen:
  - explicito: archivo dedicado (reglas_negocio.json / business_rules.json) en
    la raiz del proyecto -> SIEMPRE confianza "alta".
  - inferido_doc: heuristica de texto sobre README/docs/**/*.md -> "media".
  - inferido_codigo: nombre/proposito de flujos n8n declarados -> "baja".

Regla de seguridad (no negociable): solo las reglas de confianza "alta"
participan en gates de decision automatica (Parte 2, self_heal, generic_fixer).
Las de confianza media/baja son SOLO informativas ("revisar con negocio").

Honestidad operativa: la "verificacion funcional" de aqui es un cruce
heuristico (solapamiento de palabras clave) entre el resultado de los casos
sinteticos ya existentes (test_planner/test_executor) y las reglas explicitas.
No hay motor de ejecucion real de flujos en este proyecto -- es la misma
naturaleza estatica que el resto de las obreras de La Colmena.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, Field

from .models import (
    NormalizedFinding,
    ProjectInventory,
    RepairLoopConfig,
    SyntheticTestResult,
)
from .workers import FindingBuilder

_EXPLICIT_FILENAMES = ("reglas_negocio.json", "business_rules.json")
_RULE_LINE_PATTERNS = [
    re.compile(
        r"(?i)\b(siempre|nunca|debe|no puede|solo puede|s[oó]lo puede|"
        r"m[aá]ximo|m[ií]nimo|obligatorio|prohibido)\b"
    ),
    re.compile(r"(?i)\b(always|never|must|cannot|must not|only|maximum|minimum|required)\b"),
]
_MAX_INFERRED_DOC_RULES = 30
_STOPWORDS_MIN_LEN = 4


class BusinessRule(BaseModel):
    id: str
    descripcion: str
    origen: str  # "explicito" | "inferido_doc" | "inferido_codigo"
    confianza: str  # "alta" | "media" | "baja"
    componente_relacionado: str | None = None

    model_config = {"extra": "forbid"}


class BusinessRulesReport(BaseModel):
    reglas: list[BusinessRule] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    def alta_confianza(self) -> list[BusinessRule]:
        return [r for r in self.reglas if r.confianza == "alta"]


def _load_explicit_rules(root: Path) -> list[BusinessRule]:
    for name in _EXPLICIT_FILENAMES:
        path = root / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        items = data.get("reglas", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            continue
        out: list[BusinessRule] = []
        for idx, item in enumerate(items, start=1):
            if not isinstance(item, dict) or not item.get("descripcion"):
                continue
            out.append(BusinessRule(
                id=str(item.get("id") or f"RN-EXP-{idx:03d}"),
                descripcion=str(item["descripcion"]),
                origen="explicito",
                confianza="alta",  # las reglas explicitas SIEMPRE son alta confianza
                componente_relacionado=item.get("componente_relacionado"),
            ))
        return out
    return []


def _infer_rules_from_docs(root: Path) -> list[BusinessRule]:
    candidatos = []
    readme = root / "README.md"
    if readme.is_file():
        candidatos.append(readme)
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        candidatos.extend(sorted(docs_dir.rglob("*.md"))[:20])

    out: list[BusinessRule] = []
    seq = 0
    for doc_path in candidatos:
        try:
            text = doc_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("-*#").strip()
            if not line or len(line) > 220:
                continue
            if any(pat.search(line) for pat in _RULE_LINE_PATTERNS):
                seq += 1
                out.append(BusinessRule(
                    id=f"RN-DOC-{seq:03d}",
                    descripcion=line,
                    origen="inferido_doc",
                    confianza="media",
                ))
                if len(out) >= _MAX_INFERRED_DOC_RULES:
                    return out
    return out


def _infer_rules_from_code(root: Path, inventory: ProjectInventory) -> list[BusinessRule]:
    out: list[BusinessRule] = []
    seq = 0
    for asset in inventory.assets:
        if asset.kind != "n8n_workflow":
            continue
        try:
            wf = json.loads((root / asset.path).read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        nombre = wf.get("name") or asset.name
        if not nombre:
            continue
        seq += 1
        out.append(BusinessRule(
            id=f"RN-COD-{seq:03d}",
            descripcion=f"El flujo '{nombre}' debe cumplir el proposito implicito en su nombre/estructura.",
            origen="inferido_codigo",
            confianza="baja",
            componente_relacionado=asset.path,
        ))
    return out


def extract_business_rules(root: Path, inventory: ProjectInventory) -> BusinessRulesReport:
    """Extrae reglas de negocio. Las explicitas nunca se descartan aunque haya inferidas."""
    explicit = _load_explicit_rules(root)
    inferred = _infer_rules_from_docs(root) + _infer_rules_from_code(root, inventory)
    return BusinessRulesReport(reglas=explicit + inferred)


def business_rules_worker_findings(
    root: Path, inventory: ProjectInventory
) -> tuple[list[NormalizedFinding], BusinessRulesReport]:
    """Extrae reglas y genera un hallazgo informativo si hay inferidas (nunca gatean solas)."""
    report = extract_business_rules(root, inventory)
    builder = FindingBuilder()
    findings: list[NormalizedFinding] = []
    inferidas = [r for r in report.reglas if r.confianza != "alta"]
    if inferidas:
        findings.append(builder.make(
            severity="info",
            category="business_rule",
            title=f"{len(inferidas)} regla(s) de negocio sugerida(s) para revisar con negocio",
            description=(
                "Se infirieron reglas desde documentacion/codigo (confianza media/baja). "
                "NUNCA se usan solas para aprobar o rechazar fixes automaticos."
            ),
            evidence="; ".join(r.descripcion[:80] for r in inferidas[:5]),
            recommendation=(
                "Confirmar con negocio y, si aplican, formalizarlas en un archivo explicito "
                "(reglas_negocio.json) para que puedan participar en gates automaticos."
            ),
            source="business_rules_worker",
        ))
    return findings, report


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-záéíóúñ]+", text.lower()) if len(w) >= _STOPWORDS_MIN_LEN}


def verify_functional_against_rules(
    rules_report: BusinessRulesReport,
    results: list[SyntheticTestResult],
) -> list[NormalizedFinding]:
    """Cruza casos sinteticos fallidos contra reglas EXPLICITAS (alta confianza).

    Heuristica de solapamiento de palabras clave -- honesto: no hay ejecucion
    real de flujo aqui, es deteccion de contradiccion por texto, igual que el
    resto de los chequeos estaticos de La Colmena.
    """
    alta = rules_report.alta_confianza()
    if not alta:
        return []
    builder = FindingBuilder()
    findings: list[NormalizedFinding] = []
    fallidos = [r for r in results if not r.passed]
    for regla in alta:
        palabras_regla = _keywords(regla.descripcion)
        if not palabras_regla:
            continue
        for res in fallidos:
            evidencia = " ".join(str(f.get("message", "")) for f in res.findings)
            texto = f"{res.message} {evidencia}"
            if regla.componente_relacionado and regla.componente_relacionado.lower() not in texto.lower():
                # si la regla declara un componente, exigimos que aparezca mencionado
                if regla.componente_relacionado.lower() not in res.case_id.lower():
                    continue
            solapadas = palabras_regla & _keywords(texto)
            if len(solapadas) < 2:
                continue
            findings.append(builder.make(
                severity="critical",
                category="business_rule",
                title=f"Posible violacion de regla de negocio explicita ({regla.id})",
                description=(
                    f"El caso sintetico '{res.case_id}' fallo con un comportamiento que "
                    f"parece contradecir la regla: {regla.descripcion}"
                ),
                evidence=res.message[:300],
                impact="El flujo/agente podria estar incumpliendo una regla de negocio declarada por el equipo.",
                recommendation="Revisar el caso y ajustar la logica para respetar la regla de negocio.",
                source="business_rules_worker",
            ))
    return findings


def run_functional_verification(
    root: Path,
    inventory: ProjectInventory,
    rules_report: BusinessRulesReport,
    *,
    cases_count: int = 8,
) -> list[NormalizedFinding]:
    """Genera casos sinteticos, los ejecuta (heuristico) y cruza contra reglas explicitas.

    Reusa el generador/ejecutor de casos sinteticos ya existentes en La Colmena
    (test_planner/test_executor) -- no duplica esa logica.
    """
    if not rules_report.alta_confianza():
        return []  # sin reglas explicitas no hay nada contra que verificar (por diseno)
    from .test_executor import execute_synthetic_tests
    from .test_planner import plan_synthetic_tests

    config = RepairLoopConfig(cases_count=cases_count)
    cases = plan_synthetic_tests(inventory, config)
    results = execute_synthetic_tests(root, inventory, cases)
    return verify_functional_against_rules(rules_report, results)
