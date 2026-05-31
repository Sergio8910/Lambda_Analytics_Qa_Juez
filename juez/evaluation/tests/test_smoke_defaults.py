from __future__ import annotations

from pathlib import Path

from juez.evaluation import smoke_run


def test_smoke_usa_spec_smoke_por_defecto() -> None:
    assert Path(smoke_run.DEFAULT_SPEC_PATH).name == "spec_smoke.json"
