"""Safety gates para Auto-Fix.

El agente puede operar sin git (por ejemplo en tests o proyectos exportados).
Cuando hay un repo git limpio, estas utilidades dejan rastro con backup branch y
commits acotados a los archivos modificados por Auto-Fix.
"""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .approval_models import PatchApprovalItem
from .models import ProjectFixProposal
from .patch_models import PatchPlanItem


class SafetyGateResult(BaseModel):
    ok: bool
    message: str
    git_enabled: bool = False
    backup_branch: str | None = None
    warnings: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class GitSafetyGates:
    def __init__(self, repo_root: Path | str = ".") -> None:
        self.repo_root = Path(repo_root)

    def prepare(self, *, requested: bool = True) -> SafetyGateResult:
        if not requested:
            return SafetyGateResult(ok=True, message="Git desactivado para esta corrida.")
        if not shutil.which("git"):
            return SafetyGateResult(
                ok=True,
                message="Git no esta disponible; Auto-Fix continuara sin commits.",
                warnings=["git-no-disponible"],
            )
        if not self._git_ok("rev-parse", "--is-inside-work-tree"):
            return SafetyGateResult(
                ok=True,
                message="No hay repo git; Auto-Fix continuara sin commits.",
                warnings=["git-repo-no-detectado"],
            )
        dirty = self._git("status", "--porcelain")
        if dirty.strip():
            return SafetyGateResult(
                ok=True,
                message="Repo git con cambios previos; Auto-Fix continuara sin commits para no mezclar trabajo.",
                warnings=["git-dirty-sin-commits"],
            )
        branch = f"backup/pre-autofix-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        created = self._git_ok("branch", branch)
        if not created:
            return SafetyGateResult(
                ok=True,
                message="No se pudo crear backup branch; Auto-Fix continuara sin commits.",
                warnings=["git-backup-fallo"],
            )
        return SafetyGateResult(
            ok=True,
            message="Git listo para commits automaticos.",
            git_enabled=True,
            backup_branch=branch,
        )

    def commit_paths(self, paths: Iterable[Path], message: str) -> str | None:
        existing = [str(p) for p in paths if p.exists()]
        if not existing:
            return None
        self._git("add", "--", *existing)
        if self._git_returncode("diff", "--cached", "--quiet") == 0:
            return None
        self._git("commit", "-m", message)
        return self._git("rev-parse", "--short", "HEAD").strip() or None

    def _git_ok(self, *args: str) -> bool:
        try:
            self._git(*args)
            return True
        except Exception:
            return False

    def _git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "git command failed").strip())
        return proc.stdout

    def _git_returncode(self, *args: str) -> int:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).returncode


def can_apply_fix(proposal: ProjectFixProposal, mode: str) -> tuple[bool, str | None]:
    """Decision centralizada para reparar proyectos.

    En esta fase no se aplican cambios sobre carpetas de proyecto. ``apply-safe``
    queda reservado y degrada a propuesta para no crear una falsa sensacion de
    automatizacion.
    """
    if mode in {"dry-run", "proposal-only"}:
        return False, f"Modo {mode}: solo se generan propuestas, no se modifican archivos."
    if mode == "apply-safe":
        return False, "apply-safe aun no esta habilitado; se deja como propuesta revisable."
    if proposal.fix_type == "manual_review":
        return False, "La propuesta requiere revision humana."
    return False, "Modo de reparacion no reconocido; se bloquea por seguridad."


def can_generate_patch(proposal: ProjectFixProposal, project_path: str) -> tuple[bool, str | None]:
    """Permite solo diffs preview de bajo riesgo o reportes de revision.

    ``project_path`` queda en la firma para que futuras reglas puedan validar
    contexto del proyecto sin cambiar el contrato.
    """
    _ = project_path
    if proposal.fix_type in {"add_env_example", "add_documentation", "add_test", "manual_review"}:
        return True, None
    return (
        False,
        "Diff no generado: la propuesta modifica codigo, prompts, workflows o configuracion productiva.",
    )


def can_apply_patch_item(item: PatchPlanItem, mode: str) -> tuple[bool, str | None]:
    """Bloquea la aplicacion real de patches en esta fase."""
    if mode in {"dry-run", "proposal-only"}:
        return False, f"Modo {mode}: solo se generan diffs preview, no se modifican archivos."
    if mode == "apply-safe":
        return False, "apply-safe aun no esta habilitado para patches; queda como preview revisable."
    if item.requires_review:
        return False, "El patch requiere revision humana."
    return False, "Modo de patch no reconocido; se bloquea por seguridad."


def can_export_patch(item: PatchPlanItem) -> tuple[bool, str | None]:
    """Permite exportar solo diffs preview seguros a archivos .patch."""
    if item.status != "planned":
        return False, f"Patch no planificado: status={item.status}."
    if not item.safe_to_apply:
        return False, "Patch no marcado como seguro para exportacion."
    if item.action != "create_file":
        return False, "Solo se exportan patches de creacion de archivos nuevos."
    if item.risk not in {"low", "medium"}:
        return False, f"Riesgo no permitido para exportacion: {item.risk}."
    if not item.diff_preview:
        return False, "Patch sin diff_preview."
    if not _target_allowed_for_export(item.target_path):
        return False, f"Target no permitido para exportacion: {item.target_path}."
    return True, None


def can_mark_patch_as_applicable(approval_item: PatchApprovalItem) -> tuple[bool, str | None]:
    """Valida aprobaciones sin convertirlas en aplicacion real."""
    if approval_item.decision != "approve":
        return False, "Solo una decision approve puede marcarse como aplicable en una fase futura."
    if not approval_item.safe_to_apply:
        return False, "El patch aprobado no esta marcado como seguro."
    if approval_item.risk not in {"low", "medium"}:
        return False, f"Riesgo no permitido para aplicacion futura: {approval_item.risk}."
    if not approval_item.patch_file_path:
        return False, "Falta patch_file_path."
    if not approval_item.checksum:
        return False, "Falta checksum."
    return True, None


def _target_allowed_for_export(target_path: str | None) -> bool:
    if not target_path:
        return False
    normalized = target_path.replace("\\", "/").strip("/")
    if normalized in {
        ".env.example",
        "README_COLMENA_REVIEW.md",
        "COLMENA_TEST_PLAN.md",
        "COLMENA_REPAIR_PROPOSALS.md",
    }:
        return True
    return normalized.startswith("tests/colmena_synthetic/") or normalized.startswith(
        "colmena_generated_tests/"
    )
