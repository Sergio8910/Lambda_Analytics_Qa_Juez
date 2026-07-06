"""Reina de La Colmena: fachada principal de evaluacion y auto-fix."""
from __future__ import annotations

from pathlib import Path

from .auto_fix_agent import AutoFixAgentResult, run_auto_fix_agent
from .colmena import ColmenaResult, Componente, run_colmena
from .models import ProjectEvaluationReport
from .project_evaluator import evaluate_project_path


class ReinaColmena:
    def __init__(
        self,
        project_id: str,
        componentes: list[Componente] | None = None,
        *,
        incluir_dinamicas: bool = True,
        project_path: Path | str | None = None,
    ) -> None:
        self.project_id = project_id
        self.componentes = componentes or []
        self.incluir_dinamicas = incluir_dinamicas
        self.project_path = Path(project_path) if project_path else None

    @classmethod
    def from_project_path(
        cls,
        project_path: Path | str,
        *,
        project_id: str | None = None,
        incluir_dinamicas: bool = False,
    ) -> ReinaColmena:
        path = Path(project_path)
        return cls(
            project_id or path.resolve().name,
            [],
            incluir_dinamicas=incluir_dinamicas,
            project_path=path,
        )

    def evaluar(self) -> ColmenaResult | ProjectEvaluationReport:
        """Evalua componentes historicos o una carpeta de proyecto completo."""
        if self.project_path is not None:
            return evaluate_project_path(
                self.project_path,
                project_id=self.project_id,
                incluir_dinamicas=self.incluir_dinamicas,
            )
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
