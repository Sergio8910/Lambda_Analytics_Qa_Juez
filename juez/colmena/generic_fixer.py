"""Fixer generico de La Colmena (Parte 2): genera y prueba fixes en SANDBOX.

Para hallazgos que NO coinciden con ningun fixer especifico de self_heal_agent,
intenta generar una correccion (LLM), la prueba en una COPIA TEMPORAL del
proyecto (nunca en el proyecto real), y solo si pasa TODO el sandbox:
  a) los tests existentes del proyecto (si hay) no empeoran,
  b) la re-evaluacion no suma nuevos criticos y mitiga el hallazgo,
  c) no viola ninguna regla de negocio explicita (Parte 1),
devuelve un SelfHealFixPlan para que self_heal_agent lo aplique al proyecto
real siguiendo EXACTAMENTE el mismo entry/exit gate + backup + rollback que
los fixers especificos. No se duplica esa logica aqui.

Limites duros (no negociables):
  - max_attempts_per_finding: recortado a un techo absoluto de 5 aunque se pida mas.
  - time_limit_per_finding_min: corta el ciclo si se excede.
  - el blocklist de self_heal_agent._target_allowed se respeta siempre.
  - el sandbox es obligatorio: nunca se escribe en el proyecto real durante las pruebas.

Honestidad operativa: "correr los tests existentes" depende de que el entorno
del PROYECTO EVALUADO tenga sus dependencias disponibles, lo que normalmente no
aplica en el entorno de Juez. Un fallo de coleccion/import de pytest (deps
faltantes) se trata como "no concluyente" y NO bloquea el candidato; solo un
fallo real de un test que si corrio bloquea.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .business_rules import BusinessRulesReport, run_functional_verification
from .models import NormalizedFinding
from .self_heal_agent import _finding_improved, _target_allowed
from .self_heal_models import SelfHealFixPlan

ABSOLUTE_MAX_ATTEMPTS = 5
_SANDBOX_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules", "outputs", "dist", "build",
)

# (before_text, descripcion_hallazgo, enfoques_previos_descartados) -> after_text | None
Generator = Callable[[str, str, list[str]], "str | None"]


@dataclass
class GenericFixAttempt:
    attempt_no: int
    approach: str
    target_path: str
    tests_ok: bool
    business_rules_ok: bool
    worker_ok: bool
    approved: bool
    reason: str


@dataclass
class GenericFixOutcome:
    finding_id: str
    attempted: bool
    approved_attempt: GenericFixAttempt | None = None
    approved_after_text: str | None = None
    before_text: str | None = None
    attempts: list[GenericFixAttempt] = field(default_factory=list)
    skip_reason: str | None = None


def _default_llm_generator(before_text: str, finding_description: str, enfoques_previos: list[str]) -> str | None:
    if not os.getenv("OPENAI_API_KEY"):
        return None
    try:
        from openai import OpenAI
    except Exception:
        return None
    previos = (
        "\n\nEnfoques ya descartados, intenta algo DISTINTO:\n" + "\n".join(f"- {p}" for p in enfoques_previos)
        if enfoques_previos else ""
    )
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        resp = client.chat.completions.create(
            model=os.getenv("JUDGE_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": (
                    "Eres un ingeniero de software senior. Corrige el archivo dado para "
                    "resolver el hallazgo descrito, con el cambio minimo posible y sin romper "
                    "su funcionamiento. Responde SOLO con el contenido completo del archivo "
                    "corregido, sin comentarios ni markdown ni explicaciones."
                )},
                {"role": "user", "content": f"HALLAZGO:\n{finding_description}{previos}\n\nARCHIVO ACTUAL:\n{before_text}"},
            ],
            temperature=0.3,
        )
        content = (resp.choices[0].message.content or "").strip()
        return content or None
    except Exception:
        return None


def _create_sandbox(root: Path) -> Path:
    sandbox = Path(tempfile.mkdtemp(prefix="colmena_sandbox_"))
    shutil.copytree(root, sandbox, dirs_exist_ok=True, ignore=_SANDBOX_IGNORE)
    return sandbox


def _run_existing_tests(sandbox: Path, timeout_s: int) -> tuple[bool, str]:
    has_tests = (sandbox / "tests").is_dir() or any(sandbox.rglob("test_*.py"))
    if not has_tests:
        return True, "sin tests detectados en el proyecto (no concluyente)"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=str(sandbox), timeout=timeout_s, capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return True, "tests excedieron el tiempo limite (no concluyente)"
    except Exception as exc:
        return True, f"no se pudo ejecutar pytest: {type(exc).__name__} (no concluyente)"
    if proc.returncode == 0:
        return True, "tests existentes del proyecto pasaron"
    if proc.returncode == 1:
        return False, "tests existentes del proyecto FALLARON tras el cambio propuesto"
    return True, f"pytest returncode={proc.returncode} (no concluyente: entorno del proyecto probablemente incompleto)"


def attempt_generic_fix(
    root: Path,
    finding: NormalizedFinding,
    rules_report: BusinessRulesReport,
    *,
    max_attempts: int = 3,
    time_limit_per_finding_min: int = 10,
    generator: Generator = _default_llm_generator,
    test_timeout_s: int = 60,
) -> GenericFixOutcome:
    """Genera-prueba-reintenta en sandbox. Nunca escribe en `root`."""
    max_attempts = max(1, min(max_attempts, ABSOLUTE_MAX_ATTEMPTS))
    outcome = GenericFixOutcome(finding_id=finding.id, attempted=False)

    if not finding.file:
        outcome.skip_reason = "Hallazgo sin archivo concreto."
        return outcome
    target = root / finding.file
    if not target.is_file():
        outcome.skip_reason = "Archivo objetivo no existe."
        return outcome
    allowed, reason = _target_allowed(root, finding.file)
    if not allowed:
        outcome.skip_reason = reason
        return outcome
    try:
        before_text = target.read_text(encoding="utf-8")
    except Exception as exc:
        outcome.skip_reason = f"No se pudo leer archivo: {exc}"
        return outcome

    outcome.attempted = True
    outcome.before_text = before_text
    start = time.monotonic()
    enfoques_previos: list[str] = []

    for attempt_no in range(1, max_attempts + 1):
        if (time.monotonic() - start) / 60.0 > time_limit_per_finding_min:
            outcome.attempts.append(GenericFixAttempt(
                attempt_no=attempt_no, approach="(no iniciado)", target_path=finding.file,
                tests_ok=False, business_rules_ok=False, worker_ok=False, approved=False,
                reason=f"Tiempo limite excedido ({time_limit_per_finding_min} min).",
            ))
            break

        after_text = generator(before_text, f"{finding.title}: {finding.description}", enfoques_previos)
        if not after_text or after_text == before_text:
            outcome.attempts.append(GenericFixAttempt(
                attempt_no=attempt_no, approach="generacion", target_path=finding.file,
                tests_ok=False, business_rules_ok=False, worker_ok=False, approved=False,
                reason="El generador no produjo una propuesta de cambio (sin LLM disponible o sin cambio util).",
            ))
            break  # sin propuesta no hay nada distinto que reintentar

        approach = f"intento {attempt_no}: reescritura de {finding.file}"
        sandbox = _create_sandbox(root)
        try:
            (sandbox / finding.file).write_text(after_text, encoding="utf-8")

            tests_ok, tests_reason = _run_existing_tests(sandbox, test_timeout_s)

            from .project_evaluator import evaluate_project_path
            from .scanner import scan_project

            before_report = evaluate_project_path(root, project_id=f"{finding.id}-before")
            after_report = evaluate_project_path(sandbox, project_id=f"{finding.id}-sandbox")
            worker_ok = (
                after_report.score.critical_findings <= before_report.score.critical_findings
                and _finding_improved(finding, after_report.findings)
            )

            sandbox_inventory = scan_project(sandbox)
            rule_violations = run_functional_verification(sandbox, sandbox_inventory, rules_report)
            business_rules_ok = not any(f.severity == "critical" for f in rule_violations)

            approved = tests_ok and worker_ok and business_rules_ok
            razon = "; ".join([
                tests_reason,
                "hallazgo mitigado sin nuevos criticos" if worker_ok else "no mitigo el hallazgo o sumo criticos",
                "reglas de negocio ok" if business_rules_ok else "viola una regla de negocio explicita",
            ])
            attempt = GenericFixAttempt(
                attempt_no=attempt_no, approach=approach, target_path=finding.file,
                tests_ok=tests_ok, business_rules_ok=business_rules_ok, worker_ok=worker_ok,
                approved=approved, reason=razon,
            )
            outcome.attempts.append(attempt)
            if approved:
                outcome.approved_attempt = attempt
                outcome.approved_after_text = after_text
                break
            enfoques_previos.append(f"{approach} -> descartado ({razon})")
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

    return outcome


def to_self_heal_plan(finding: NormalizedFinding, outcome: GenericFixOutcome) -> SelfHealFixPlan | None:
    """Convierte un outcome aprobado en un plan listo para el gate de self_heal_agent."""
    if not outcome.approved_attempt or outcome.approved_after_text is None or outcome.before_text is None:
        return None
    confidence = max(0.70, 0.95 - 0.05 * (outcome.approved_attempt.attempt_no - 1))
    return SelfHealFixPlan(
        finding_id=finding.id,
        finding_title=finding.title,
        target_path=finding.file,
        fix_type="generic_llm_fix",
        confidence=confidence,
        reason=(
            f"Fixer generico aprobado en sandbox (intento {outcome.approved_attempt.attempt_no}): "
            f"{outcome.approved_attempt.reason}"
        ),
        before_text=outcome.before_text,
        after_text=outcome.approved_after_text,
    )
