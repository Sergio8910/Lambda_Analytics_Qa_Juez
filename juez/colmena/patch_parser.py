"""Parser limitado para unified diffs de archivos nuevos."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .patch_apply_models import ParsedPatch


class PatchParseError(ValueError):
    """Patch no compatible con la fase create_file_only."""


def parse_new_file_patch(
    *,
    patch_id: str,
    proposal_id: str,
    patch_file_path: Path | str,
) -> ParsedPatch:
    path = Path(patch_file_path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if len(lines) < 3:
        raise PatchParseError("Patch demasiado corto.")
    old_headers = [line for line in lines if line.startswith("--- ")]
    new_headers = [line for line in lines if line.startswith("+++ ")]
    if len(old_headers) != 1 or len(new_headers) != 1:
        raise PatchParseError("Solo se soporta un archivo por patch.")
    if old_headers[0].strip() != "--- /dev/null":
        raise PatchParseError("Solo se soporta creacion de archivos nuevos desde /dev/null.")

    target_path = new_headers[0][4:].strip()
    if not target_path:
        raise PatchParseError("Patch sin target path.")
    if Path(target_path).is_absolute() or ".." in Path(target_path).parts:
        raise PatchParseError("Target path absoluto o con path traversal.")

    try:
        hunk_index = next(i for i, line in enumerate(lines) if line.startswith("@@ "))
    except StopIteration as exc:
        raise PatchParseError("Patch sin hunk unified diff.") from exc

    content_lines: list[str] = []
    for line in lines[hunk_index + 1 :]:
        if line.startswith("--- ") or line.startswith("+++ ") or line.startswith("@@ "):
            raise PatchParseError("No se soportan multiples hunks o multiples archivos.")
        if line.startswith("-"):
            raise PatchParseError("No se soportan lineas removidas.")
        if line.startswith("+"):
            content_lines.append(line[1:])
            continue
        if line == r"\ No newline at end of file":
            continue
        raise PatchParseError("Solo se soportan lineas agregadas en patches create_file.")

    return ParsedPatch(
        patch_id=patch_id,
        proposal_id=proposal_id,
        patch_file_path=str(path),
        target_path=target_path,
        action="create_file",
        content="\n".join(content_lines) + ("\n" if content_lines else ""),
        checksum=_sha256_file(path),
    )


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
