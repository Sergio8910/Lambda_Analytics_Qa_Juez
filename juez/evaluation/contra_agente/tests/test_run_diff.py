"""Tests del modulo run_diff.

Comando estandar:
    python -m pytest juez/evaluation/contra_agente/tests/test_run_diff.py \
        -v --tb=short -p no:xdist -p no:rerunfailures
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from juez.evaluation.contra_agente.models import (
    BatchResult,
    ConversationResult,
    TurnResult,
)
from juez.evaluation.contra_agente.run_diff import (
    cargar_run_previo,
    generar_diff,
    persistir_run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _turn(passed: bool = True, score: float = 0.9) -> TurnResult:
    return TurnResult(
        turn_id=1,
        turn_type="opener",
        message_sent="hola",
        agent_response="hola",
        latency_ms=10.0,
        scores={"task_success": score},
        passed=passed,
        reason="ok" if passed else "fail",
    )


def _conv(
    plan_id: str,
    category: str = "happy_path",
    passed: bool = True,
    overall_score: float = 0.9,
) -> ConversationResult:
    return ConversationResult(
        plan_id=plan_id,
        category=category,
        tags=[category],
        passed=passed,
        turn_results=[_turn(passed=passed, score=overall_score)],
        collapse_turn=None,
        overall_score=overall_score,
        transcript=[{"role": "user", "content": "hola"}],
        latency_total_ms=10.0,
        diagnosis="ok",
        artifact_verdict=None,
    )


def _batch(
    agent_id: str = "agente_test",
    batch_id: str = "batch_test",
    pass_rate: float = 0.8,
    results: Optional[List[ConversationResult]] = None,
    scorecard: Optional[Dict[str, float]] = None,
) -> BatchResult:
    results = results or [_conv("conv_01", passed=True, overall_score=0.9)]
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    return BatchResult(
        batch_id=batch_id,
        agent_id=agent_id,
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=pass_rate,
        by_category={"happy_path": {"total": total, "passed": passed, "pass_rate": pass_rate}},
        collapse_pattern={},
        results=results,
        recommendations=[],
        scorecard=scorecard or {"calidad_prompt": 0.8, "tools_integraciones": 0.7},
        cost_summary=None,
    )


@pytest.fixture
def tmp_out_dir():
    d = tempfile.mkdtemp(prefix="run_diff_test_")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests de persistencia
# ---------------------------------------------------------------------------


def test_persistir_y_round_trip(tmp_out_dir):
    """persistir_run guarda y luego se puede leer el contenido equivalente."""
    br = _batch(agent_id="agente_rt", pass_rate=0.75)
    path = persistir_run(br, agent_id="agente_rt", out_dir=tmp_out_dir)

    assert os.path.exists(path)
    p = Path(path)
    # Esta dentro de tmp_out_dir/agente_rt/
    assert p.parent.name == "agente_rt"
    assert p.suffix == ".json"

    with open(path, "r", encoding="utf-8") as fp:
        data = json.load(fp)

    assert data["agent_id"] == "agente_rt"
    assert data["batch_id"] == "batch_test"
    assert data["pass_rate"] == pytest.approx(0.75)
    assert "results" in data and len(data["results"]) == 1


def test_cargar_run_previo_con_0_o_1_archivos_devuelve_none(tmp_out_dir):
    """Sin archivos o con uno solo, no hay 'previo' valido."""
    # Sin archivos
    assert cargar_run_previo("agente_x", out_dir=tmp_out_dir) is None

    # Con un solo archivo
    br = _batch(agent_id="agente_x")
    persistir_run(br, agent_id="agente_x", out_dir=tmp_out_dir)
    assert cargar_run_previo("agente_x", out_dir=tmp_out_dir) is None


def test_cargar_run_previo_devuelve_penultimo(tmp_out_dir):
    """Con N archivos, devuelve el penultimo por orden de nombre."""
    agent = "agente_p"
    br1 = _batch(agent_id=agent, batch_id="b1", pass_rate=0.50)
    br2 = _batch(agent_id=agent, batch_id="b2", pass_rate=0.70)
    br3 = _batch(agent_id=agent, batch_id="b3", pass_rate=0.90)

    # Persistir en orden y forzar nombres distintos por orden
    persistir_run(br1, agent_id=agent, out_dir=tmp_out_dir)
    # Renombrar manualmente para asegurar orden lexicografico estable
    _renombrar_ultimo(tmp_out_dir, agent, "20260101T000000.json")

    persistir_run(br2, agent_id=agent, out_dir=tmp_out_dir)
    _renombrar_ultimo(tmp_out_dir, agent, "20260102T000000.json")

    persistir_run(br3, agent_id=agent, out_dir=tmp_out_dir)
    _renombrar_ultimo(tmp_out_dir, agent, "20260103T000000.json")

    previo = cargar_run_previo(agent, out_dir=tmp_out_dir)
    assert previo is not None
    # El penultimo en orden cronologico es br2 (b2)
    assert previo["batch_id"] == "b2"
    assert previo["pass_rate"] == pytest.approx(0.70)


def _renombrar_ultimo(out_dir: str, agent_id: str, new_name: str) -> None:
    """Renombra el archivo mas reciente del directorio para tener nombre determinista."""
    target = Path(out_dir) / agent_id
    files = sorted(target.iterdir(), key=lambda p: p.stat().st_mtime)
    if not files:
        return
    files[-1].rename(target / new_name)


# ---------------------------------------------------------------------------
# Tests de diff
# ---------------------------------------------------------------------------


def test_diff_previo_none_marca_primera_corrida():
    """Si no hay previo, generar_diff retorna {primera_corrida: True}."""
    br = _batch()
    actual = br.model_dump()
    out = generar_diff(actual, None)
    assert out.get("primera_corrida") is True
    assert "actual" in out
    assert out["actual"]["agent_id"] == "agente_test"


def test_diff_previo_igual_a_actual_todos_deltas_cero_y_estable():
    """Con datos identicos, todos los deltas en cero y label ESTABLE."""
    br = _batch(pass_rate=0.8)
    actual = br.model_dump()
    previo = br.model_dump()

    diff = generar_diff(actual, previo)

    assert diff["global_score"]["delta"] == pytest.approx(0.0)
    assert diff["global_score"]["label"] == "ESTABLE"
    for dim_name, dim_data in diff["dimensiones"].items():
        assert dim_data["delta"] == pytest.approx(0.0), f"dim {dim_name} no esta en delta=0"
    assert diff["regresiones"] == []
    assert diff["mejoras"] == []
    assert diff["casos_nuevos"] == []
    assert diff["casos_perdidos"] == []


def test_diff_global_score_mejora():
    """Si pass_rate sube, label = MEJORA."""
    br_previo = _batch(pass_rate=0.50)
    br_actual = _batch(pass_rate=0.80)

    diff = generar_diff(br_actual.model_dump(), br_previo.model_dump())

    assert diff["global_score"]["actual"] == pytest.approx(0.80)
    assert diff["global_score"]["previo"] == pytest.approx(0.50)
    assert diff["global_score"]["delta"] == pytest.approx(0.30)
    assert diff["global_score"]["label"] == "MEJORA"


def test_diff_global_score_regresion():
    """Si pass_rate baja, label = REGRESION."""
    br_previo = _batch(pass_rate=0.90)
    br_actual = _batch(pass_rate=0.40)

    diff = generar_diff(br_actual.model_dump(), br_previo.model_dump())

    assert diff["global_score"]["label"] == "REGRESION"
    assert diff["global_score"]["delta"] < 0


def test_diff_detecta_regresion_por_plan():
    """Caso que pasaba con score alto antes y ahora cae mas de 10 pts → regresion."""
    previo_results = [
        _conv("conv_01", passed=True, overall_score=0.95),
        _conv("conv_02", passed=True, overall_score=0.90),
    ]
    actual_results = [
        _conv("conv_01", passed=False, overall_score=0.40),  # cae 55 pts → regresion
        _conv("conv_02", passed=True, overall_score=0.92),   # estable
    ]
    br_previo = _batch(results=previo_results, pass_rate=1.0)
    br_actual = _batch(results=actual_results, pass_rate=0.5)

    diff = generar_diff(br_actual.model_dump(), br_previo.model_dump())

    regs = diff["regresiones"]
    assert len(regs) == 1
    assert regs[0]["plan_id"] == "conv_01"
    assert regs[0]["delta"] <= -10.0
    assert regs[0]["actual_score"] < regs[0]["previo_score"]


def test_diff_detecta_mejora_por_plan():
    """Caso que mejora mas de 10 pts aparece en 'mejoras'."""
    previo_results = [_conv("conv_01", passed=False, overall_score=0.30)]
    actual_results = [_conv("conv_01", passed=True, overall_score=0.90)]
    br_previo = _batch(results=previo_results, pass_rate=0.0)
    br_actual = _batch(results=actual_results, pass_rate=1.0)

    diff = generar_diff(br_actual.model_dump(), br_previo.model_dump())

    mejoras = diff["mejoras"]
    assert len(mejoras) == 1
    assert mejoras[0]["plan_id"] == "conv_01"
    assert mejoras[0]["delta"] >= 10.0


def test_diff_detecta_casos_nuevos_y_perdidos():
    """plan_id en actual pero no en previo → casos_nuevos; al reves → casos_perdidos."""
    previo_results = [
        _conv("conv_old", passed=True, overall_score=0.9),
        _conv("conv_shared", passed=True, overall_score=0.8),
    ]
    actual_results = [
        _conv("conv_shared", passed=True, overall_score=0.8),
        _conv("conv_new1", passed=True, overall_score=0.85),
        _conv("conv_new2", passed=True, overall_score=0.95),
    ]
    br_previo = _batch(results=previo_results, pass_rate=1.0)
    br_actual = _batch(results=actual_results, pass_rate=1.0)

    diff = generar_diff(br_actual.model_dump(), br_previo.model_dump())

    assert "conv_new1" in diff["casos_nuevos"]
    assert "conv_new2" in diff["casos_nuevos"]
    assert "conv_shared" not in diff["casos_nuevos"]
    assert "conv_old" in diff["casos_perdidos"]
    assert "conv_shared" not in diff["casos_perdidos"]


def test_diff_dimensiones_solo_incluye_metrics_en_ambos():
    """Si una dimension solo aparece en uno de los dos, no entra en el diff."""
    br_previo = _batch(scorecard={"a": 0.5, "b": 0.6})
    br_actual = _batch(scorecard={"a": 0.7, "c": 0.4})  # b solo en previo, c solo en actual

    diff = generar_diff(br_actual.model_dump(), br_previo.model_dump())

    assert "a" in diff["dimensiones"]
    assert "b" not in diff["dimensiones"]
    assert "c" not in diff["dimensiones"]
    assert diff["dimensiones"]["a"]["delta"] == pytest.approx(0.2)
