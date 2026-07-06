from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from juez.colmena.iteration_loop import run_project_repair_loop
from juez.colmena.models import RepairLoopConfig, RepairLoopResult
from juez.colmena.patch_models import PatchPlanItem
from juez.colmena.patch_planner import build_patch_plan
from juez.colmena.patch_report import render_patch_plan_report, write_patch_plan_outputs
from juez.colmena.patch_validator import validate_patch_item


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


def _file_snapshot(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _repair(root: Path, mode: str = "dry-run") -> RepairLoopResult:
    return run_project_repair_loop(
        root,
        RepairLoopConfig(cases_count=6, max_iterations=1, repair_mode=mode),
        output_dir=root.parent / "outputs",
    )


def test_patch_plan_generates_env_example_diff_without_modifying_project() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        before = _file_snapshot(root)
        plan = build_patch_plan(_repair(root))
        after = _file_snapshot(root)
        env_item = next(item for item in plan.items if item.target_path == ".env.example")

        assert before == after
        assert not (root / ".env.example").exists()
        assert env_item.status == "planned"
        assert env_item.safe_to_apply is True
        assert env_item.diff_preview
        assert env_item.diff_preview.startswith("--- /dev/null")
        assert "+++ .env.example" in env_item.diff_preview
        assert plan.fixes_applied == 0
        assert plan.files_modified == 0


def test_patch_plan_blocks_risky_existing_project_modifications() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        plan = build_patch_plan(_repair(root))

        blocked = [item for item in plan.items if item.status == "blocked"]
        assert blocked
        assert any("codigo" in (item.blocked_reason or "") for item in blocked)
        assert all(item.diff_preview is None for item in blocked)


def test_patch_validator_blocks_sensitive_and_existing_targets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        _write(root / "README_COLMENA_REVIEW.md", "ya existe\n")
        sensitive = PatchPlanItem(
            proposal_id="FIX-SENSITIVE",
            action="create_file",
            status="planned",
            target_path=".env",
            risk="high",
            safe_to_apply=True,
            requires_review=True,
            reason="crear secreto real",
            source="manual_review",
        )
        existing = sensitive.model_copy(
            update={"proposal_id": "FIX-EXISTING", "target_path": "README_COLMENA_REVIEW.md"}
        )

        assert validate_patch_item(sensitive, root).status == "blocked"
        assert validate_patch_item(existing, root).status == "blocked"


def test_patch_plan_apply_safe_still_applies_zero_fixes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        before = _file_snapshot(root)
        plan = build_patch_plan(_repair(root, mode="apply-safe"), mode="apply-safe")

        assert _file_snapshot(root) == before
        assert plan.mode == "apply-safe"
        assert plan.fixes_applied == 0
        assert plan.files_modified == 0
        assert all(any("apply-safe" in note for note in item.validation_notes) for item in plan.items)


def test_patch_plan_outputs_txt_and_json_reports() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = _weak_project(Path(tmp))
        plan = write_patch_plan_outputs(build_patch_plan(_repair(root)), output_dir=Path(tmp) / "reports")
        report = render_patch_plan_report(plan)

        assert plan.txt_report_path and Path(plan.txt_report_path).exists()
        assert plan.json_report_path and Path(plan.json_report_path).exists()
        assert "SAFE FIX DIFF PLANNER" in report
        assert "Files modified    : 0" in report
        payload = json.loads(Path(plan.json_report_path).read_text(encoding="utf-8"))
        assert payload["fixes_applied"] == 0
        assert payload["files_modified"] == 0


def test_empty_patch_plan_has_zero_items() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = RepairLoopResult(
            project_path=str(Path(tmp)),
            config=RepairLoopConfig(),
            final_verdict="passed",
            fix_proposals=[],
        )
        plan = build_patch_plan(result)

        assert plan.total_items == 0
        assert plan.safe_items == 0
        assert plan.blocked_items == 0


def test_cli_accepts_generate_diffs_for_project_folder() -> None:
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
                "4",
                "--max-iterations",
                "1",
                "--generate-diffs",
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
        assert "SAFE FIX DIFF PLANNER" in proc.stdout
        assert "Fixes applied     : 0" in proc.stdout
        assert not (root / ".env.example").exists()


def test_colmena_module_cli_accepts_generate_diffs() -> None:
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
                "--generate-diffs",
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr
        assert "SAFE FIX DIFF PLANNER" in proc.stdout


def test_legacy_json_flow_ignores_generate_diffs() -> None:
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
                "--generate-diffs",
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
        assert "SAFE FIX DIFF PLANNER" not in proc.stdout
        assert "PROJECT REPAIR LOOP" not in proc.stdout
