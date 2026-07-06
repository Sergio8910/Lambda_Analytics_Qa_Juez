"""Planificador de pruebas sinteticas para La Colmena."""
from __future__ import annotations

from .models import ProjectInventory, RepairLoopConfig, SyntheticTestCase
from .scenario_generator import generate_synthetic_cases


def plan_synthetic_tests(
    inventory: ProjectInventory,
    config: RepairLoopConfig,
) -> list[SyntheticTestCase]:
    return generate_synthetic_cases(inventory, config.cases_count)
