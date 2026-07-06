from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from juez.colmena.apply_report import write_patch_apply_report
from juez.colmena.approval_manifest import build_approval_manifest, write_approval_manifest
from juez.colmena.iteration_loop import run_project_repair_loop
from juez.colmena.models import RepairLoopConfig
from juez.colmena.patch_applier import apply_approved_patches
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


def _approval_manifest(base: Path, decisions: dict[int, str | None] | None = None) -> tuple[Path, Path]:
    root = _weak_project(base)
    repair = run_project_repair_loop(
        root,
        RepairLoopConfig(cases_count=6, max_iterations=1, repair_mode="dry-run"),
        output_dir=base / "outputs",
    )
    plan = build_patch_plan(repair)
    export_result = export_patch_plan_items(plan.items, project_path=root, output_dir=base / "outputs")
    manifest = write_approval_manifest(build_approval_manifest(export_result), output_dir=base / "outputs")
    payload = asdict(manifest)
    for index, decision in (decisions or {}).items():
        payload["items"][index]["decision"] = decision
    approval_file = base / "approval.json"
    approval_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return root, approval_file


def _manual_approval(
    base: Path,
    target_path: str,
    diff: str,
    *,
    decision: str | None = "approve",
) -> tuple[Path, Path]:
    root = _weak_project(base)
    patch_path = base / "outputs" / "patches" / "manual.patch"
    _write(patch_path, diff)
    payload = {
        "project_path": str(root),
        "generated_at": "2026-07-06T00:00:00+00:00",
        "approval_required": True,
        "items": [
            {
                "proposal_id": "FIX-MANUAL",
                "patch_id": "patch_manual",
                "target_path": target_path,
                "patch_file_path": str(patch_path),
                "status": "pending",
                "decision": decision,
                "reviewer": "qa",
                "reviewed_at": None,
                "reason": None,
                "risk": "low",
                "safe_to_apply": True,
                "checksum": _sha256_file(patch_path),
            }
        ],
    }
    approval_file = base / "approval.json"
    approval_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return root, approval_file


def test_apply_approved_patch_creates_allowed_new_file_and_outputs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root, approval_file = _approval_manifest(base, {0: "approve"})
        result = write_patch_apply_report(
            apply_approved_patches(project_path=root, approval_file=approval_file, output_dir=base / "outputs"),
            output_dir=base / "outputs",
        )

        assert result.approval_valid is True
        assert result.applied_items == 1
        assert result.files_created == 1
        assert result.files_modified == 0
        assert result.files_overwritten == 0
        assert Path(result.backup_path or "").exists()
        assert Path(result.audit_log_path or "").exists()
        assert Path(result.txt_report_path or "").exists()
        assert Path(result.json_report_path or "").exists()
        assert result.post_evaluation_executed is True
        assert (root / result.item_results[0].target_path).exists()


def test_rejected_and_pending_patches_are_not_applied() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root, approval_file = _approval_manifest(base, {0: "reject", 1: None})
        result = apply_approved_patches(project_path=root, approval_file=approval_file, output_dir=base / "outputs")

        assert result.applied_items == 0
        assert result.skipped_items >= 2
        assert result.files_created == 0
        assert not any(item.created_file for item in result.item_results)


def test_invalid_checksum_blocks_application() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root, approval_file = _approval_manifest(base, {0: "approve"})
        payload = json.loads(approval_file.read_text(encoding="utf-8"))
        payload["items"][0]["checksum"] = "sha256:bad"
        approval_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        result = apply_approved_patches(project_path=root, approval_file=approval_file, output_dir=base / "outputs")

        assert result.approval_valid is False
        assert result.applied_items == 0
        assert result.files_created == 0


def test_existing_target_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root, approval_file = _approval_manifest(base, {0: "approve"})
        payload = json.loads(approval_file.read_text(encoding="utf-8"))
        _write(root / payload["items"][0]["target_path"], "ya existe\n")

        result = apply_approved_patches(project_path=root, approval_file=approval_file, output_dir=base / "outputs")

        assert result.applied_items == 0
        assert result.blocked_items >= 1
        assert "ya existe" in (root / payload["items"][0]["target_path"]).read_text(encoding="utf-8")


def test_path_traversal_patch_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        diff = "--- /dev/null\n+++ ../evil.md\n@@ -0,0 +1,1 @@\n+bad\n"
        root, approval_file = _manual_approval(Path(tmp), "../evil.md", diff)

        result = apply_approved_patches(project_path=root, approval_file=approval_file, output_dir=Path(tmp) / "outputs")

        assert result.applied_items == 0
        assert result.blocked_items >= 1
        assert not (root.parent / "evil.md").exists()


def test_patch_that_modifies_existing_file_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        diff = "--- README.md\n+++ README.md\n@@ -1,1 +1,1 @@\n-old\n+new\n"
        root, approval_file = _manual_approval(Path(tmp), "README.md", diff)

        result = apply_approved_patches(project_path=root, approval_file=approval_file, output_dir=Path(tmp) / "outputs")

        assert result.applied_items == 0
        assert result.blocked_items >= 1


def test_real_env_target_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        diff = "--- /dev/null\n+++ .env\n@@ -0,0 +1,1 @@\n+SECRET=x\n"
        root, approval_file = _manual_approval(Path(tmp), ".env", diff)

        result = apply_approved_patches(project_path=root, approval_file=approval_file, output_dir=Path(tmp) / "outputs")

        assert result.applied_items == 0
        assert not (root / ".env").exists()


def test_source_code_target_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        diff = "--- /dev/null\n+++ src/new_feature.py\n@@ -0,0 +1,1 @@\n+print('no')\n"
        root, approval_file = _manual_approval(Path(tmp), "src/new_feature.py", diff)

        result = apply_approved_patches(project_path=root, approval_file=approval_file, output_dir=Path(tmp) / "outputs")

        assert result.applied_items == 0
        assert not (root / "src" / "new_feature.py").exists()


def test_cli_apply_approved_patches_requires_approval_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root = _weak_project(base)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        proc = subprocess.run(
            [sys.executable, "-m", "juez", "colmena", "--project", str(root), "--apply-approved-patches"],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        assert proc.returncode != 0
        assert "--apply-approved-patches requiere --approval-file" in (proc.stderr + proc.stdout)


def test_cli_accepts_apply_approved_patches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root, approval_file = _approval_manifest(base, {0: "approve"})
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
                str(approval_file),
                "--apply-approved-patches",
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr
        assert "APPLY APPROVED PATCHES" in proc.stdout
        assert "Files modified             : 0" in proc.stdout
        assert "Applied patches            : 1" in proc.stdout


def test_legacy_json_flow_does_not_apply_patches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        config = base / "legacy.json"
        _write(config, json.dumps({"project_id": "legacy", "componentes": []}))
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        proc = subprocess.run(
            [sys.executable, "-m", "juez", "colmena", "--project", str(config), "--apply-approved-patches"],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr
        assert "LA COLMENA - REPORTE DE PROYECTO" in proc.stdout
        assert "APPLY APPROVED PATCHES" not in proc.stdout


def test_colmena_module_cli_supports_apply_approved_patches() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root, approval_file = _approval_manifest(base, {0: "approve"})
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "juez.colmena",
                "--project",
                str(root),
                "--approval-file",
                str(approval_file),
                "--apply-approved-patches",
            ],
            cwd=base,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

        assert proc.returncode == 0, proc.stderr
        assert "APPLY APPROVED PATCHES" in proc.stdout
        assert "Files created              : 1" in proc.stdout


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
