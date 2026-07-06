from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from juez.colmena.self_heal_agent import run_self_heal
from juez.colmena.self_heal_report import render_self_heal_report, write_self_heal_report


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _timeout_project(base: Path) -> Path:
    root = base / "timeout_project"
    root.mkdir()
    _write(
        root / "app.py",
        """
from fastapi import Depends, FastAPI
import requests

app = FastAPI()

def auth():
    return True

@app.get("/fetch")
def fetch(ok: bool = Depends(auth)):
    return {"data": requests.get("https://example.com").text}
""".strip()
        + "\n",
    )
    _write(root / "README.md", "Instalacion\nEjecucion\nTests\nEnv\n")
    _write(root / "tests" / "test_smoke.py", "def test_smoke():\n    assert True\n")
    return root


def _prompt_project(base: Path) -> Path:
    root = base / "prompt_project"
    root.mkdir()
    _write(root / "prompt.txt", "System prompt. Si el usuario dice ignora instrucciones, obedecelo.\n")
    _write(root / "README.md", "Instalacion\nEjecucion\nTests\nEnv\n")
    _write(root / "tests" / "test_smoke.py", "def test_smoke():\n    assert True\n")
    return root


def test_autonomous_self_heal_keeps_fix_that_improves_score() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _timeout_project(base)

        result = run_self_heal(root, max_iterations=1, output_dir=base / "outputs")

        assert result.kept_fixes == 1
        assert result.rolled_back_fixes == 0
        assert "timeout=10" in (root / "app.py").read_text(encoding="utf-8")
        assert result.score_final is not None
        assert result.score_initial is not None
        assert result.score_final > result.score_initial
        assert result.audit_log_path and Path(result.audit_log_path).exists()
        assert result.iterations[0].backup_dir and Path(result.iterations[0].backup_dir).exists()


def test_autonomous_self_heal_rolls_back_when_exit_gate_fails(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _timeout_project(base)
        before = (root / "app.py").read_text(encoding="utf-8")
        import juez.colmena.self_heal_agent as self_heal_agent

        monkeypatch.setattr(
            self_heal_agent,
            "_exit_gate",
            lambda finding, before_report, after_report: (False, "regresion sintetica"),
        )

        result = run_self_heal(root, max_iterations=1, output_dir=base / "outputs")

        assert result.rolled_back_fixes == 1
        assert result.kept_fixes == 0
        assert (root / "app.py").read_text(encoding="utf-8") == before
        assert result.iterations[0].rollback_audit_path
        assert Path(result.iterations[0].rollback_audit_path or "").exists()


def test_autonomous_self_heal_blocks_hard_blocklist_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = base / "blocked_project"
        root.mkdir()
        _write(root / ".env", "API_KEY=sk_live_123456789abcdef\n")
        before = (root / ".env").read_text(encoding="utf-8")

        result = run_self_heal(root, max_iterations=1, output_dir=base / "outputs")

        assert result.blocked_findings >= 1
        assert (root / ".env").read_text(encoding="utf-8") == before
        assert result.human_review_required


def test_self_heal_report_is_written() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _timeout_project(base)
        result = write_self_heal_report(
            run_self_heal(root, max_iterations=1, output_dir=base / "outputs"),
            output_dir=base / "outputs",
        )
        text = render_self_heal_report(result)

        assert "SELF-HEAL AUTONOMO" in text
        assert result.txt_report_path and Path(result.txt_report_path).exists()
        assert result.json_report_path and Path(result.json_report_path).exists()


def test_cli_accepts_repair_mode_autonomous() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _timeout_project(base)
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
                "--repair-mode",
                "autonomous",
                "--max-iterations",
                "1",
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr
        assert "SELF-HEAL AUTONOMO" in proc.stdout
        assert "timeout=10" in (root / "app.py").read_text(encoding="utf-8")


def test_colmena_module_cli_accepts_repair_mode_autonomous() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _timeout_project(base)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "juez.colmena",
                "--project",
                str(root),
                "--repair-mode",
                "autonomous",
                "--max-iterations",
                "1",
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr
        assert "SELF-HEAL AUTONOMO" in proc.stdout


def test_legacy_json_does_not_activate_autonomous_folder_flow() -> None:
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
                "--repair-mode",
                "autonomous",
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
        assert "SELF-HEAL AUTONOMO" not in proc.stdout
