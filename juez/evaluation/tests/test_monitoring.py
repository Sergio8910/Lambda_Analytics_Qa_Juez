"""Tests del monitoreo programado (sin red: usa targets de tipo 'prompt')."""
from __future__ import annotations

import json
import os
import tempfile

from juez.monitoring import load_config, run_monitoring_pass
from juez.monitoring.monitor import MonitorConfig, MonitorTarget, evaluate_target


def _outdir() -> str:
    return tempfile.mkdtemp(prefix="juez_mon_")


def _config(out: str) -> MonitorConfig:
    return MonitorConfig(
        interval_seconds=60,
        output_dir=out,
        targets=[
            MonitorTarget(
                kind="prompt",
                name="Agente Demo",
                prompt="Eres un asistente de soporte. Responde en español, claro y con pasos.",
            ),
        ],
    )


def test_pass_evalua_y_guarda_reportes():
    res = run_monitoring_pass(_config(_outdir()))
    assert res["total"] == 1
    assert res["ok"] == 1
    assert res["error"] == 0
    r = res["resultados"][0]
    assert r["status"] == "ok"
    assert r["score"] is not None
    assert os.path.exists(r["reporte_path"])
    assert os.path.exists(res["resumen_path"])
    assert "RESUMEN DE MONITOREO" in res["resumen_txt"]


def test_kind_no_soportado_es_error():
    t = MonitorTarget(kind="inexistente", name="X")
    r = evaluate_target(t, _outdir(), "2026-01-01T00:00:00+00:00")
    assert r["status"] == "error"
    assert "kind no soportado" in r["error"]


def test_load_config():
    d = _outdir()
    cfg_path = os.path.join(d, "cfg.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({
            "interval_seconds": 1800,
            "output_dir": os.path.join(d, "out"),
            "targets": [{"kind": "prompt", "name": "P1", "prompt": "Eres un bot."}],
        }, f)
    cfg = load_config(cfg_path)
    assert cfg.interval_seconds == 1800
    assert len(cfg.targets) == 1
    assert cfg.targets[0].kind == "prompt"


def test_un_target_error_no_tumba_la_pasada():
    cfg = MonitorConfig(
        output_dir=_outdir(),
        targets=[
            MonitorTarget(kind="prompt", name="Bueno", prompt="Eres un asistente claro y útil."),
            MonitorTarget(kind="n8n", name="Malo", workflow_id="id-invalido-xyz"),
        ],
    )
    res = run_monitoring_pass(cfg)
    assert res["total"] == 2
    assert res["ok"] >= 1
    assert res["error"] >= 1
