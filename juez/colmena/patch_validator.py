"""Validaciones conservadoras para planes de patch."""
from __future__ import annotations

from pathlib import Path

from .patch_models import PatchPlanItem

_SENSITIVE_FILENAMES = {".env", ".env.local", ".env.prod", ".env.production", "secrets.json"}
_BLOCKED_SUFFIXES = {".py", ".json"}
_BLOCKED_PATTERNS = ("workflow", "prompt", "docker-compose", "dockerfile")


def validate_patch_item(item: PatchPlanItem, project_path: Path | str) -> PatchPlanItem:
    root = Path(project_path).resolve()
    notes = list(item.validation_notes)
    target = item.target_path or ""

    if item.action != "create_file":
        return item.model_copy(
            update={
                "status": "blocked",
                "safe_to_apply": False,
                "requires_review": True,
                "blocked_reason": "Esta fase solo permite previews para archivos nuevos de bajo riesgo.",
                "validation_notes": notes + ["modify/skip real bloqueado en esta fase"],
            }
        )

    if not target:
        return item.model_copy(
            update={
                "status": "not_applicable",
                "safe_to_apply": False,
                "requires_review": True,
                "blocked_reason": "La propuesta no define target_path.",
                "validation_notes": notes + ["target_path faltante"],
            }
        )

    if _escapes_root(root, target):
        return item.model_copy(
            update={
                "status": "blocked",
                "safe_to_apply": False,
                "requires_review": True,
                "blocked_reason": "La ruta del patch intenta salir de la carpeta evaluada.",
                "validation_notes": notes + ["path traversal bloqueado"],
            }
        )

    target_path = root / target
    lower_name = target_path.name.lower()
    lower_target = target.lower()
    if target_path.exists():
        return item.model_copy(
            update={
                "status": "blocked",
                "safe_to_apply": False,
                "requires_review": True,
                "blocked_reason": "El archivo destino ya existe; no se sobrescribe nada en esta fase.",
                "validation_notes": notes + ["archivo existente no se modifica"],
            }
        )
    if lower_name in _SENSITIVE_FILENAMES or lower_target.endswith(".env"):
        return item.model_copy(
            update={
                "status": "blocked",
                "safe_to_apply": False,
                "requires_review": True,
                "blocked_reason": "No se crean ni modifican archivos de secretos reales.",
                "validation_notes": notes + ["archivo sensible bloqueado"],
            }
        )
    if target_path.suffix.lower() in _BLOCKED_SUFFIXES and "colmena" not in lower_target:
        return item.model_copy(
            update={
                "status": "blocked",
                "safe_to_apply": False,
                "requires_review": True,
                "blocked_reason": "No se generan patches directos sobre codigo/configuracion existente.",
                "validation_notes": notes + ["extension de riesgo bloqueada"],
            }
        )
    if any(pattern in lower_target for pattern in _BLOCKED_PATTERNS) and "colmena" not in lower_target:
        return item.model_copy(
            update={
                "status": "blocked",
                "safe_to_apply": False,
                "requires_review": True,
                "blocked_reason": "No se modifican workflows, prompts ni despliegue en esta fase.",
                "validation_notes": notes + ["target sensible bloqueado"],
            }
        )
    return item.model_copy(
        update={
            "status": "planned",
            "safe_to_apply": item.safe_to_apply,
            "requires_review": item.requires_review,
            "validation_notes": notes + ["patch seguro para preview; no aplicado"],
        }
    )


def _escapes_root(root: Path, target: str) -> bool:
    try:
        (root / target).resolve().relative_to(root)
        return False
    except ValueError:
        return True
