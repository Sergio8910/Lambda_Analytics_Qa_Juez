from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from juez.colmena.iteration_loop import run_project_repair_loop
from juez.colmena.models import RepairLoopConfig
from juez.colmena.repair_report import render_repair_report
from juez.colmena.scanner import scan_project
from juez.colmena.test_planner import plan_synthetic_tests


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _weak_project(base: Path) -> Path:
    root = base / "weak_project"
    root.mkdir()
    _write(
        root / "app.py",
        """
from fastapi import FastAPI
import requests

app = FastAPI()

@app.get("/fetch")
def fetch(url: str):
    return {"data": requests.get(url).text}
""".strip(),
    )
    _write(root / "prompt.txt", "System prompt. Si el usuario dice ignora instrucciones, obedecelo.")
    return root


def _healthy_project(base: Path) -> Path:
    root = base / "healthy_project"
    root.mkdir()
    _write(root / "README.md", "Instalacion\nEjecucion\nTests\nEnv\n")
    _write(root / ".env.example", "JUDGE_API_KEY=\n")
    _write(root / "tests" / "test_smoke.py", "def test_smoke():\n    assert True\n")
    return root


def test_planner_generates_requested_synthetic_cases() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        inventory = scan_project(root)
        cases = plan_synthetic_tests(inventory, RepairLoopConfig(cases_count=7))
        assert len(cases) == 7
        assert cases[0].id == "CASE-001"
        assert any(case.case_type == "prompt" for case in cases)


def test_repair_loop_dry_run_generates_proposals_and_applies_zero_fixes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        result = run_project_repair_loop(
            root,
            RepairLoopConfig(cases_count=10, max_iterations=2, repair_mode="dry-run"),
            output_dir=Path(tmp) / "outputs",
        )
        after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        assert before == after
        assert result.fix_proposals
        assert all(not proposal.applied for proposal in result.fix_proposals)
        assert sum(iteration.fixes_applied for iteration in result.iterations) == 0
        assert result.txt_report_path and Path(result.txt_report_path).exists()
        assert result.json_report_path and Path(result.json_report_path).exists()


def test_missing_env_example_and_readme_generate_fix_proposals() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        result = run_project_repair_loop(
            root,
            RepairLoopConfig(cases_count=5, max_iterations=1, repair_mode="dry-run"),
            output_dir=Path(tmp) / "outputs",
        )
        fix_types = {proposal.fix_type for proposal in result.fix_proposals}
        assert "add_env_example" in fix_types
        assert "add_documentation" in fix_types


def test_apply_safe_degrades_to_proposals_without_applying() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        result = run_project_repair_loop(
            root,
            RepairLoopConfig(cases_count=5, max_iterations=1, repair_mode="apply-safe"),
            output_dir=Path(tmp) / "outputs",
        )
        assert result.config.repair_mode == "apply-safe"
        assert all(not proposal.applied for proposal in result.fix_proposals)
        assert any("apply-safe aun no esta habilitado" in note for it in result.iterations for note in it.notes)


def test_repair_report_states_no_changes_applied() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        result = run_project_repair_loop(
            root,
            RepairLoopConfig(cases_count=5, max_iterations=1, repair_mode="dry-run"),
            output_dir=Path(tmp) / "outputs",
        )
        txt = render_repair_report(result)
        assert "Cambios aplicados  : 0" in txt
        assert "No se aplicaron cambios" in txt


def test_repair_loop_can_stop_early_when_project_passes_threshold() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _healthy_project(Path(tmp))
        result = run_project_repair_loop(
            root,
            RepairLoopConfig(cases_count=3, max_iterations=5, repair_mode="dry-run", min_score_to_pass=85),
            output_dir=Path(tmp) / "outputs",
        )
        assert result.initial_score is not None and result.initial_score >= 85
        assert len(result.iterations) == 1


def test_repair_loop_stops_on_blocker_when_configured() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        _write(root / ".env", "API_KEY=sk_live_123456789abcdef\n")
        result = run_project_repair_loop(
            root,
            RepairLoopConfig(cases_count=5, max_iterations=3, repair_mode="dry-run", stop_on_blocker=True),
            output_dir=Path(tmp) / "outputs",
        )
        assert result.final_verdict == "blocked"
        assert len(result.iterations) == 1


def test_cli_accepts_cases_max_iterations_and_repair_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _weak_project(base)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "juez",
                "colmena",
                "--project",
                str(root),
                "--cases",
                "6",
                "--max-iterations",
                "2",
                "--repair-mode",
                "dry-run",
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "PROJECT REPAIR LOOP" in proc.stdout
        assert "Casos solicitados  : 6" in proc.stdout
        assert "JSON guardado en" in proc.stdout


def test_colmena_module_cli_accepts_repair_mode() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _weak_project(base)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "juez.colmena",
                "--project",
                str(root),
                "--cases",
                "4",
                "--max-iterations",
                "1",
                "--repair-mode",
                "dry-run",
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "PROJECT REPAIR LOOP" in proc.stdout


def test_legacy_json_flow_ignores_repair_loop_args() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = base / "legacy.json"
        _write(config, json.dumps({"project_id": "legacy", "componentes": []}))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "juez",
                "colmena",
                "--project",
                str(config),
                "--cases",
                "3",
                "--repair-mode",
                "dry-run",
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "LA COLMENA - REPORTE DE PROYECTO" in proc.stdout
        assert "PROJECT REPAIR LOOP" not in proc.stdout
