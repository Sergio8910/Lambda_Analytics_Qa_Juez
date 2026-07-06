from __future__ import annotations

import tempfile
from pathlib import Path

from juez.colmena.business_rules import BusinessRulesReport
from juez.colmena.generic_fixer import (
    ABSOLUTE_MAX_ATTEMPTS,
    GenericFixAttempt,
    GenericFixOutcome,
    attempt_generic_fix,
    to_self_heal_plan,
)
from juez.colmena.project_evaluator import evaluate_project_path
from juez.colmena.self_heal_agent import run_self_heal

_FIXED = (
    "from fastapi import FastAPI, Depends\n\n"
    "def auth():\n    return True\n\n"
    "app = FastAPI()\n\n"
    "@app.get('/data')\n"
    "def data(ok: bool = Depends(auth)):\n"
    "    return {'ok': True}\n"
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _no_auth_project(base: Path) -> Path:
    root = base / "no_auth_project"
    root.mkdir()
    _write(root / "app.py", (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n\n"
        "@app.get('/data')\n"
        "def data():\n"
        "    return {'ok': True}\n"
    ))
    _write(root / "README.md", "Instalacion\nEjecucion\nTests\nEnv\n")
    return root


def _no_auth_finding(root: Path):
    report = evaluate_project_path(root)
    return next(f for f in report.findings if f.category == "api" and "autenticacion" in f.title.lower())


def test_generic_fixer_approves_candidate_that_resolves_finding() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _no_auth_project(Path(tmp)).resolve()
        finding = _no_auth_finding(root)

        outcome = attempt_generic_fix(
            root, finding, BusinessRulesReport(reglas=[]),
            generator=lambda before, desc, previos: _FIXED,
        )

        assert outcome.attempted is True
        assert outcome.approved_attempt is not None
        assert outcome.attempts[0].tests_ok is True  # sin tests -> no concluyente == True
        assert outcome.attempts[0].worker_ok is True
        assert outcome.attempts[0].business_rules_ok is True
        # el proyecto REAL no se toco durante las pruebas de sandbox
        assert (root / "app.py").read_text(encoding="utf-8") != _FIXED

        plan = to_self_heal_plan(finding, outcome)
        assert plan is not None
        assert plan.fix_type == "generic_llm_fix"
        assert plan.after_text == _FIXED
        assert plan.confidence >= 0.90  # aprobado al primer intento


def test_generic_fixer_no_candidate_when_generator_returns_none() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _no_auth_project(Path(tmp)).resolve()
        finding = _no_auth_finding(root)

        outcome = attempt_generic_fix(
            root, finding, BusinessRulesReport(reglas=[]),
            generator=lambda before, desc, previos: None,
        )

        assert outcome.attempted is True
        assert outcome.approved_attempt is None
        assert len(outcome.attempts) == 1
        assert to_self_heal_plan(finding, outcome) is None


def test_generic_fixer_respects_absolute_attempt_ceiling() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _no_auth_project(Path(tmp)).resolve()
        finding = _no_auth_finding(root)

        outcome = attempt_generic_fix(
            root, finding, BusinessRulesReport(reglas=[]),
            generator=lambda before, desc, previos: before + "\n# intento fallido\n",
            max_attempts=99,  # se recorta al techo absoluto
        )

        assert outcome.attempted is True
        assert outcome.approved_attempt is None
        assert len(outcome.attempts) == ABSOLUTE_MAX_ATTEMPTS


def test_generic_fixer_skips_blocklisted_target() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "blocked"
        root.mkdir()
        _write(root / ".env", "API_KEY=sk_live_123\n")
        from juez.colmena.models import NormalizedFinding

        finding = NormalizedFinding(
            id="SEC-001", severity="critical", category="security",
            title="secreto", description="x", file=".env", source="x",
        )
        outcome = attempt_generic_fix(root, finding, BusinessRulesReport(reglas=[]))
        assert outcome.attempted is False
        assert outcome.skip_reason is not None


def test_run_self_heal_applies_generic_fixer_result_through_normal_gates(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _no_auth_project(base)

        import juez.colmena.generic_fixer as generic_fixer_module

        def fake_attempt_generic_fix(root_, finding, rules_report, **kwargs):
            before = (root_ / finding.file).read_text(encoding="utf-8")
            attempt = GenericFixAttempt(
                attempt_no=1, approach="fake", target_path=finding.file,
                tests_ok=True, business_rules_ok=True, worker_ok=True,
                approved=True, reason="fake approved",
            )
            return GenericFixOutcome(
                finding_id=finding.id, attempted=True, approved_attempt=attempt,
                approved_after_text=_FIXED, before_text=before, attempts=[attempt],
            )

        monkeypatch.setattr(generic_fixer_module, "attempt_generic_fix", fake_attempt_generic_fix)

        result = run_self_heal(
            root, max_iterations=1, enable_generic_fixer=True, output_dir=base / "outputs"
        )

        assert result.kept_fixes == 1
        assert (root / "app.py").read_text(encoding="utf-8") == _FIXED
        assert result.iterations[0].fix_type == "generic_llm_fix"
        assert result.iterations[0].generic_fixer_attempts


def test_run_self_heal_generic_fixer_disabled_keeps_manual_review_behavior() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _no_auth_project(base)

        result = run_self_heal(root, max_iterations=1, output_dir=base / "outputs")

        # Sin --enable-generic-fixer, el hallazgo de "sin autenticacion" (sin fixer
        # especifico) debe seguir cayendo a revision humana, como antes.
        assert result.blocked_findings >= 1 or result.human_review_required
        assert result.iterations[0].generic_fixer_attempts == []
