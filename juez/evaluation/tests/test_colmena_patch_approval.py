from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from juez.colmena.approval_manifest import build_approval_manifest, write_approval_manifest
from juez.colmena.approval_validator import validate_approval_file
from juez.colmena.iteration_loop import run_project_repair_loop
from juez.colmena.models import RepairLoopConfig, RepairLoopResult
from juez.colmena.patch_exporter import export_patch_plan_items
from juez.colmena.patch_planner import build_patch_plan


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


def _snapshot(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _artifacts(base: Path, mode: str = "dry-run"):
    root = _weak_project(base)
    repair = run_project_repair_loop(
        root,
        RepairLoopConfig(cases_count=6, max_iterations=1, repair_mode=mode),
        output_dir=base / "outputs",
    )
    plan = build_patch_plan(repair, mode=mode)
    export_result = export_patch_plan_items(plan.items, project_path=root, output_dir=base / "outputs")
    manifest = write_approval_manifest(build_approval_manifest(export_result), output_dir=base / "outputs")
    return root, plan, export_result, manifest


def test_export_patches_generates_patch_files_and_manifest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root, _plan, export_result, manifest = _artifacts(base)

        assert export_result.exported_items > 0
        assert export_result.files_modified == 0
        assert export_result.fixes_applied == 0
        assert manifest.manifest_path and Path(manifest.manifest_path).exists()
        assert manifest.pending_items == export_result.exported_items
        assert manifest.fixes_applied == 0
        assert all(item.checksum for item in manifest.items)
        assert all(Path(item.patch_file_path or "").exists() for item in manifest.items)
        assert not (root / ".env.example").exists()


def test_export_patches_does_not_modify_evaluated_project() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _weak_project(base)
        before = _snapshot(root)
        repair = run_project_repair_loop(
            root,
            RepairLoopConfig(cases_count=6, max_iterations=1, repair_mode="dry-run"),
            output_dir=base / "outputs",
        )
        plan = build_patch_plan(repair)
        export_patch_plan_items(plan.items, project_path=root, output_dir=base / "outputs")

        assert _snapshot(root) == before


def test_valid_approval_file_is_validated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _root, _plan, _export_result, manifest = _artifacts(base)
        payload = asdict(manifest)
        payload["items"][0]["decision"] = "approve"
        payload["items"][0]["reviewer"] = "qa"
        approval_file = base / "approved.json"
        approval_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        result = validate_approval_file(approval_file)

        assert result.valid is True
        assert result.approved_items == 1
        assert result.pending_items == len(payload["items"]) - 1
        assert result.files_modified == 0
        assert result.fixes_applied == 0


def test_invalid_decision_generates_clear_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _root, _plan, _export_result, manifest = _artifacts(base)
        payload = asdict(manifest)
        payload["items"][0]["decision"] = "accepted"
        approval_file = base / "invalid.json"
        approval_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        result = validate_approval_file(approval_file)

        assert result.valid is False
        assert any("invalid decision" in error for error in result.errors)


def test_missing_patch_file_generates_clear_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _root, _plan, _export_result, manifest = _artifacts(base)
        payload = asdict(manifest)
        payload["items"][0]["patch_file_path"] = str(base / "outputs" / "patches" / "missing.patch")
        approval_file = base / "missing_patch.json"
        approval_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        result = validate_approval_file(approval_file)

        assert result.valid is False
        assert any("Patch file does not exist" in error for error in result.errors)


def test_checksum_mismatch_generates_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _root, _plan, _export_result, manifest = _artifacts(base)
        patch_path = Path(manifest.items[0].patch_file_path or "")
        patch_path.write_text(patch_path.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

        result = validate_approval_file(Path(manifest.manifest_path or ""))

        assert result.valid is False
        assert any("checksum mismatch" in error for error in result.errors)


def test_no_eligible_patches_does_not_fail() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        result = RepairLoopResult(
            project_path=str(base),
            config=RepairLoopConfig(),
            final_verdict="passed",
            fix_proposals=[],
        )
        plan = build_patch_plan(result)
        export_result = export_patch_plan_items(plan.items, project_path=base, output_dir=base / "outputs")
        manifest = write_approval_manifest(build_approval_manifest(export_result), output_dir=base / "outputs")

        assert export_result.exported_items == 0
        assert manifest.total_items == 0
        assert manifest.manifest_path and Path(manifest.manifest_path).exists()


def test_apply_safe_export_still_applies_zero_fixes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root, plan, export_result, _manifest = _artifacts(base, mode="apply-safe")

        assert plan.mode == "apply-safe"
        assert plan.fixes_applied == 0
        assert plan.files_modified == 0
        assert export_result.fixes_applied == 0
        assert export_result.files_modified == 0
        assert not (root / ".env.example").exists()


def test_cli_accepts_export_patches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _weak_project(base)
        before = _snapshot(root)
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
                "--export-patches",
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr
        assert "PATCH EXPORT & APPROVAL GATE" in proc.stdout
        assert "Patch files generated" in proc.stdout
        assert _snapshot(root) == before
        assert list((base / "outputs" / "patches").glob("*.patch"))
        assert list((base / "outputs").glob("colmena_patch_approval_*.json"))


def test_cli_accepts_approval_file_without_exporting_new_patch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root, _plan, _export_result, manifest = _artifacts(base)
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
                "--approval-file",
                str(manifest.manifest_path),
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr
        assert "Approval file provided      : true" in proc.stdout
        assert "Approval file validated     : true" in proc.stdout
        assert "Fixes applied               : 0" in proc.stdout


def test_legacy_json_flow_does_not_activate_patch_exporter() -> None:
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
                "--export-patches",
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
        assert "PATCH EXPORT & APPROVAL GATE" not in proc.stdout
        assert not (base / "outputs" / "patches").exists()


def test_colmena_module_cli_supports_export_patches() -> None:
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
                "--export-patches",
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr
        assert "PATCH EXPORT & APPROVAL GATE" in proc.stdout
        assert "Files modified              : 0" in proc.stdout
