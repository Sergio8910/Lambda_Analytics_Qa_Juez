"""Ejecutor sintetico/no destructivo del repair loop."""
from __future__ import annotations

import json
from pathlib import Path

from .models import ProjectInventory, SyntheticTestCase, SyntheticTestResult


def execute_synthetic_tests(
    project_root: Path,
    inventory: ProjectInventory,
    cases: list[SyntheticTestCase],
) -> list[SyntheticTestResult]:
    return [_execute_case(project_root, inventory, case) for case in cases]


def _execute_case(
    project_root: Path,
    inventory: ProjectInventory,
    case: SyntheticTestCase,
) -> SyntheticTestResult:
    if case.case_type == "config" and case.target_path == ".env.example":
        return _exists_case(case, project_root / ".env.example", "No existe .env.example.")
    if case.case_type == "generic_project" and case.target_path == "README.md":
        return _readme_case(case, project_root)
    if case.case_type == "generic_project" and case.target_path == "tests":
        passed = inventory.detected_assets.get("tests", 0) > 0
        return SyntheticTestResult(
            case_id=case.id,
            passed=passed,
            score=100 if passed else 15,
            message="Se detectaron tests." if passed else "No se detectaron tests automatizados.",
            evidence=[f"tests={inventory.detected_assets.get('tests', 0)}"],
            findings=[] if passed else [_finding("missing_tests", "medium", "No hay tests detectados.")],
        )
    if case.case_type == "api":
        return _api_case(project_root, case)
    if case.case_type == "prompt":
        return _prompt_case(project_root, case)
    if case.case_type == "n8n_workflow":
        return _n8n_case(project_root, case)
    return SyntheticTestResult(
        case_id=case.id,
        passed=True,
        score=80,
        message="Caso sintetico revisado por inventario sin bloqueos especificos.",
        evidence=["dry-run: no se ejecuto ningun servicio externo"],
    )


def _exists_case(case: SyntheticTestCase, path: Path, fail_message: str) -> SyntheticTestResult:
    passed = path.exists()
    return SyntheticTestResult(
        case_id=case.id,
        passed=passed,
        score=100 if passed else 0,
        message="Archivo requerido presente." if passed else fail_message,
        evidence=[str(path)],
        findings=[] if passed else [_finding("missing_env_example", "medium", fail_message)],
    )


def _readme_case(case: SyntheticTestCase, root: Path) -> SyntheticTestResult:
    readme = next((p for p in root.iterdir() if p.is_file() and p.name.lower() in {"readme.md", "readme.rst"}), None)
    if readme is None:
        return SyntheticTestResult(
            case_id=case.id,
            passed=False,
            score=0,
            message="No existe README en la raiz.",
            evidence=[str(root)],
            findings=[_finding("missing_documentation", "medium", "README faltante.")],
        )
    text = readme.read_text(encoding="utf-8", errors="ignore").lower()
    required = ["instal", "ejec", "test", "env"]
    missing = [item for item in required if item not in text]
    passed = not missing
    return SyntheticTestResult(
        case_id=case.id,
        passed=passed,
        score=100 if passed else 55,
        message="README operativo suficiente." if passed else f"README incompleto: faltan {', '.join(missing)}.",
        evidence=[readme.name],
        findings=[] if passed else [_finding("missing_documentation", "low", "README incompleto.")],
    )


def _api_case(root: Path, case: SyntheticTestCase) -> SyntheticTestResult:
    text = _read_target(root, case.target_path)
    issues: list[dict] = []
    evidence: list[str] = []
    if "timeout" in (case.title + (case.expected_behavior or "")).lower() and "requests." in text and "timeout=" not in text:
        issues.append(_finding("weak_error_handling", "medium", "Llamada HTTP sin timeout."))
        evidence.append("requests.* sin timeout=")
    if "Depends(" not in text and "Security(" not in text and "@app." in text:
        issues.append(_finding("manual_review_required", "high", "Autenticacion no evidente en endpoints."))
        evidence.append("endpoint FastAPI sin Depends/Security")
    passed = not issues
    return SyntheticTestResult(
        case_id=case.id,
        passed=passed,
        score=100 if passed else 40,
        message="API cumple el caso sintetico." if passed else "API requiere endurecimiento antes de produccion.",
        evidence=evidence,
        findings=issues,
    )


def _prompt_case(root: Path, case: SyntheticTestCase) -> SyntheticTestResult:
    text = _read_target(root, case.target_path).lower()
    has_role = any(token in text for token in ("eres", "rol", "objetivo", "system"))
    has_boundaries = any(token in text for token in ("no reveles", "no compartas", "rechaza", "fuera de alcance", "seguridad"))
    vulnerable = "ignora instrucciones" in text or "ignore previous instructions" in text
    issues = []
    if not has_role or not has_boundaries or vulnerable:
        issues.append(_finding("weak_prompt_boundaries", "high", "Prompt con limites/rol insuficientes."))
    passed = not issues
    return SyntheticTestResult(
        case_id=case.id,
        passed=passed,
        score=100 if passed else 35,
        message="Prompt con rol y limites suficientes." if passed else "Prompt requiere limites y guardrails claros.",
        evidence=[case.target_path or "(sin ruta)"],
        findings=issues,
    )


def _n8n_case(root: Path, case: SyntheticTestCase) -> SyntheticTestResult:
    workflow = _load_json(root, case.target_path)
    nodes = workflow.get("nodes", []) if isinstance(workflow, dict) else []
    has_error_strategy = any(
        node.get("retryOnFail") or node.get("continueOnFail") or node.get("onError")
        for node in nodes
        if isinstance(node, dict)
    )
    has_credentials = any(bool(node.get("credentials")) for node in nodes if isinstance(node, dict))
    issues = []
    if "timeout" in case.title.lower() and not has_error_strategy:
        issues.append(_finding("n8n_missing_error_branch", "medium", "Workflow sin retry/onError evidente."))
    if "credencial" in case.title.lower() and has_credentials and not has_error_strategy:
        issues.append(_finding("weak_error_handling", "medium", "Credenciales sin estrategia de degradacion/error."))
    passed = not issues
    return SyntheticTestResult(
        case_id=case.id,
        passed=passed,
        score=100 if passed else 45,
        message="Workflow con senales minimas de resiliencia." if passed else "Workflow requiere rama de error/retry.",
        evidence=[case.target_path or "(sin ruta)", f"nodes={len(nodes)}"],
        findings=issues,
    )


def _read_target(root: Path, rel: str | None) -> str:
    if not rel:
        return ""
    path = root / rel
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _load_json(root: Path, rel: str | None) -> dict:
    text = _read_target(root, rel)
    try:
        data = json.loads(text)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _finding(category: str, severity: str, message: str) -> dict:
    return {"category": category, "severity": severity, "message": message}
