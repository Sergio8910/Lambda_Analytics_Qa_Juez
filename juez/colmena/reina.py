"""Reina de La Colmena: fachada principal de evaluacion y auto-fix."""
from __future__ import annotations

from pathlib import Path

from .auto_fix_agent import AutoFixAgentResult, run_auto_fix_agent
from .colmena import ColmenaResult, Componente, run_colmena


class ReinaColmena:
    def __init__(
        self,
        project_id: str,
        componentes: list[Componente],
        *,
        incluir_dinamicas: bool = True,
    ) -> None:
        self.project_id = project_id
        self.componentes = componentes
        self.incluir_dinamicas = incluir_dinamicas

    def evaluar(self) -> ColmenaResult:
        """Fallback: solo evalua el proyecto, sin aplicar cambios."""
        return run_colmena(self.project_id, self.componentes, incluir_dinamicas=self.incluir_dinamicas)

    def evaluar_y_arreglar(
        self,
        *,
        project_file: Path | str | None = None,
        max_iteraciones: int = 5,
        min_confidence: float = 0.80,
        apply_changes: bool = True,
        git: bool = True,
        repo_root: Path | str = ".",
        output_dir: Path | str = "outputs",
    ) -> AutoFixAgentResult:
        """Evalua, aplica fixes automaticos, re-evalua e itera."""
        return run_auto_fix_agent(
            self.project_id,
            self.componentes,
            project_file=project_file,
            max_iteraciones=max_iteraciones,
            min_confidence=min_confidence,
            incluir_dinamicas=self.incluir_dinamicas,
            apply_changes=apply_changes,
            git=git,
            repo_root=repo_root,
            output_dir=output_dir,
        )
