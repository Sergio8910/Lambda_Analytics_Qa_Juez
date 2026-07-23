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

import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .business_rules import BusinessRulesReport, run_functional_verification
from .models import NormalizedFinding
from .self_heal_agent import _finding_improved, _target_allowed
from .self_heal_models import SelfHealFixPlan

ABSOLUTE_MAX_ATTEMPTS = 5
_SANDBOX_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules", "outputs", "dist", "build",
)

@dataclass
class GenericFixContext:
    root_path: str
    target_path: str
    finding_id: str
    finding_title: str
    finding_severity: str
    finding_category: str
    finding_description: str
    finding_evidence: str
    finding_recommendation: str
    business_rules: list[dict[str, str]]
    previous_rejected_approaches: list[str]


@dataclass
class GenericFixProposal:
    after_text: str
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated_cost_usd: float | None = None


# Nuevo contrato preferido:
#   generator(before_text, context: GenericFixContext) -> GenericFixProposal | str | None
# Contrato historico, preservado para stubs deterministas:
#   generator(before_text, descripcion_hallazgo, enfoques_previos) -> str | None
Generator = Callable[..., "GenericFixProposal | str | None"]


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
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None


@dataclass
class GenericFixOutcome:
    finding_id: str
    attempted: bool
    approved_attempt: GenericFixAttempt | None = None
    approved_after_text: str | None = None
    before_text: str | None = None
    attempts: list[GenericFixAttempt] = field(default_factory=list)
    skip_reason: str | None = None


def _default_llm_generator(before_text: str, context: GenericFixContext) -> GenericFixProposal | None:
    from juez.settings import settings

    from juez.llm_client import make_chat_client, api_key_presente
    if not api_key_presente():
        return None
    model = os.getenv("COLMENA_GENERIC_FIXER_MODEL", settings.JUDGE_MODEL)
    timeout_s = float(os.getenv("COLMENA_GENERIC_FIXER_TIMEOUT_S", "45"))
    max_tokens = int(os.getenv("COLMENA_GENERIC_FIXER_MAX_OUTPUT_TOKENS", "4096"))
    prompt = _build_llm_prompt(before_text, context)
    try:
        client = make_chat_client(api_key=settings.OPENAI_API_KEY, timeout=timeout_s, max_retries=0)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": (
                    "Eres un ingeniero de software senior trabajando dentro de un sandbox. "
                    "Propón el cambio mínimo para resolver el hallazgo sin tocar credenciales, "
                    "control plane, pricing, pagos, migraciones ni esquema. Devuelve JSON válido "
                    'con una sola clave "after_text", cuyo valor sea el contenido COMPLETO del '
                    "archivo corregido. Si no hay una propuesta segura, after_text debe ser null."
                )},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or ""
        after_text = _parse_after_text(content)
        if not after_text:
            return None
        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage is not None else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage is not None else None
        return GenericFixProposal(
            after_text=after_text,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=_estimate_cost(model, prompt_tokens, completion_tokens),
        )
    except Exception:
        return None


def _build_llm_prompt(before_text: str, context: GenericFixContext) -> str:
    payload = {
        "finding": {
            "id": context.finding_id,
            "title": context.finding_title,
            "severity": context.finding_severity,
            "category": context.finding_category,
            "description": context.finding_description,
            "evidence": context.finding_evidence,
            "recommendation": context.finding_recommendation,
        },
        "target_path": context.target_path,
        "business_rules_high_confidence": context.business_rules,
        "previous_rejected_approaches": context.previous_rejected_approaches,
        "file_context": {
            "instruction": "Reescribe solo este archivo y conserva el comportamiento no relacionado.",
            "current_full_text": before_text,
        },
        "output_contract": {
            "after_text": "contenido completo del archivo corregido, o null si no hay cambio seguro",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_after_text(content: str) -> str | None:
    raw = content.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        stripped = _strip_markdown_fence(raw)
        return stripped or None
    if isinstance(data, dict):
        value = data.get("after_text")
        return value if isinstance(value, str) and value.strip() else None
    return None


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _estimate_cost(model: str | None, prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    if prompt_tokens is None and completion_tokens is None:
        return None
    try:
        from juez.evaluation.contra_agente.synthetic.cost_meter import CostMeter

        meter = CostMeter()
        meter.track(model or "unknown", prompt_tokens or 0, completion_tokens or 0)
        return float(meter.summary()["total_cost_usd"])
    except Exception:
        return None


def _business_rules_payload(rules_report: BusinessRulesReport) -> list[dict[str, str]]:
    return [
        {
            "id": rule.id,
            "descripcion": rule.descripcion,
            "origen": rule.origen,
            "confianza": rule.confianza,
            "componente_relacionado": rule.componente_relacionado or "",
        }
        for rule in rules_report.alta_confianza()
    ]


def _call_generator(
    generator: Generator,
    before_text: str,
    context: GenericFixContext,
) -> GenericFixProposal | None:
    try:
        params_count = len(inspect.signature(generator).parameters)
    except (TypeError, ValueError):
        params_count = 2
    if params_count >= 3:
        result = generator(
            before_text,
            f"{context.finding_title}: {context.finding_description}",
            context.previous_rejected_approaches,
        )
    else:
        result = generator(before_text, context)
    if result is None:
        return None
    if isinstance(result, GenericFixProposal):
        return result
    if isinstance(result, str):
        return GenericFixProposal(after_text=result) if result.strip() else None
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
        context = GenericFixContext(
            root_path=str(root),
            target_path=finding.file,
            finding_id=finding.id,
            finding_title=finding.title,
            finding_severity=finding.severity,
            finding_category=finding.category,
            finding_description=finding.description,
            finding_evidence=finding.evidence,
            finding_recommendation=finding.recommendation,
            business_rules=_business_rules_payload(rules_report),
            previous_rejected_approaches=list(enfoques_previos),
        )
        if (time.monotonic() - start) / 60.0 > time_limit_per_finding_min:
            outcome.attempts.append(GenericFixAttempt(
                attempt_no=attempt_no, approach="(no iniciado)", target_path=finding.file,
                tests_ok=False, business_rules_ok=False, worker_ok=False, approved=False,
                reason=f"Tiempo limite excedido ({time_limit_per_finding_min} min).",
            ))
            break

        proposal = _call_generator(generator, before_text, context)
        after_text = proposal.after_text if proposal is not None else None
        usage_kwargs = _usage_kwargs(proposal)
        if not after_text or after_text == before_text:
            outcome.attempts.append(GenericFixAttempt(
                attempt_no=attempt_no, approach="generacion", target_path=finding.file,
                tests_ok=False, business_rules_ok=False, worker_ok=False, approved=False,
                reason="El generador no produjo una propuesta de cambio (sin LLM disponible o sin cambio util).",
                **usage_kwargs,
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
                approved=approved, reason=razon, **usage_kwargs,
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


def _usage_kwargs(proposal: GenericFixProposal | None) -> dict[str, Any]:
    if proposal is None:
        return {}
    total = None
    if proposal.prompt_tokens is not None or proposal.completion_tokens is not None:
        total = (proposal.prompt_tokens or 0) + (proposal.completion_tokens or 0)
    return {
        "model": proposal.model,
        "prompt_tokens": proposal.prompt_tokens,
        "completion_tokens": proposal.completion_tokens,
        "total_tokens": total,
        "estimated_cost_usd": proposal.estimated_cost_usd,
    }


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
