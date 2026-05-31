"""End-to-end real contra la API del Juez con credenciales del .env.

Sube un job real de n8n (workflow del usuario) y polling hasta completion.
NO mockea nada — usa OpenAI + N8N reales. Cuesta unos centavos en GPT.

Skippea automáticamente si faltan keys.
"""
from __future__ import annotations

import os
import time

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv()

WORKFLOW_URL = "https://n8n-dev.lambdaanalytics.co/workflow/BP8nDCvZMRvP2Jyf"
ELEVEN_AGENT_ID = "agent_9101kmg85qvsere9k2h5jm9tfdw1"
ELEVEN_BRANCH_ID = "agtbrch_7901kmg85schfn2bg6a0es52gh6b"  # branch "Main" del agente Lía


@pytest.fixture(scope="module")
def client() -> TestClient:
    from juez.api.main import app
    return TestClient(app)


def _poll_until_done(client: TestClient, job_id: str, timeout_s: int = 600) -> dict:
    """Polling cada 3s hasta que el job termine o se acabe el timeout."""
    deadline = time.time() + timeout_s
    last_progress = ""
    while time.time() < deadline:
        resp = client.get(f"/api/v1/evaluate/{job_id}")
        assert resp.status_code == 200, f"GET falló: {resp.status_code}"
        data = resp.json()
        if data.get("progress"):
            cur = f"{data['progress'].get('percent', 0)}% — {data['progress'].get('step', '')}"
            if cur != last_progress:
                print(f"  [{data['status']}] {cur}")
                last_progress = cur
        if data["status"] in ("completed", "failed"):
            return data
        time.sleep(3)
    pytest.fail(f"Timeout esperando el job {job_id}")


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or not os.getenv("N8N_API_KEY"),
    reason="Faltan OPENAI_API_KEY o N8N_API_KEY",
)
def test_e2e_n8n_real(client: TestClient) -> None:
    """Lanza una evaluación real del flujo n8n del usuario."""
    resp = client.post(
        "/api/v1/evaluate/n8n",
        json={
            "flow": {"url": WORKFLOW_URL},
            "total_conversaciones": 0,  # solo estático + GPT, sin contra-agente (más rápido y barato)
        },
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    print(f"\n=== n8n real job: {job_id} ===")

    final = _poll_until_done(client, job_id, timeout_s=300)
    print(f"\n=== FINAL status: {final['status']} ===")

    if final["status"] == "failed":
        pytest.fail(f"Job falló:\n{final.get('error', '(sin error)')}")

    assert final["status"] == "completed"
    result = final["result"]
    assert result["kind"] == "n8n"
    assert "score_general" in result
    assert "scores" in result
    assert "trigger" in result
    assert "reporte_txt" in result
    assert "reporte_path" in result
    assert len(result["reporte_txt"]) > 100  # debe tener contenido

    print(f"\nNombre: {result['nombre']}")
    print(f"Score general: {result['score_general']:.1f}%")
    print(f"Trigger tipo: {result.get('trigger', {}).get('tipo', '?')}")
    print(f"Trigger testeable: {result.get('trigger', {}).get('testeable', '?')}")
    print(f"Problemas detectados: {len(result.get('problemas', []))}")
    print(f"Reporte guardado en: {result['reporte_path']}")


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or not os.getenv("ELEVENLABS_API_KEY"),
    reason="Faltan OPENAI_API_KEY o ELEVENLABS_API_KEY",
)
def test_e2e_elevenlabs_branch_with_n8n_discovery(client: TestClient) -> None:
    """Branch + descubrimiento automático de flujos n8n -> pipeline unificado."""
    resp = client.post(
        "/api/v1/evaluate/elevenlabs",
        json={
            "target_id": ELEVEN_BRANCH_ID,
            "include_n8n_flows": True,
            "total_conversaciones": 0,
        },
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    print(f"\n=== eleven BRANCH+n8n job: {job_id} ===")

    final = _poll_until_done(client, job_id, timeout_s=420)
    print(f"\n=== FINAL status: {final['status']} ===")

    if final["status"] == "failed":
        pytest.fail(f"Job falló:\n{final.get('error', '(sin error)')}")

    assert final["status"] == "completed"
    result = final["result"]
    assert result["kind"] == "elevenlabs_branch"
    assert "branch" in result
    assert result["branch"]["branch_id"] == ELEVEN_BRANCH_ID
    assert result["branch"]["agent_id"] == ELEVEN_AGENT_ID
    assert "n8n_discovery" in result
    assert "nodos" in result  # estructura tipo pipeline

    print(f"\nBranch name        : {result['branch']['branch_name']}")
    print(f"Agente padre       : {result['branch']['agent_id']}")
    print(f"URLs salientes     : {len(result['n8n_discovery']['urls_salientes'])}")
    print(f"Matches a flujos   : {len(result['n8n_discovery']['matches'])}")
    print(f"Sin match          : {len(result['n8n_discovery']['sin_match'])}")
    print(f"Externos           : {len(result['n8n_discovery']['externos'])}")
    print(f"Nodos en pipeline  : {len(result.get('nodos', []))}")
    print(f"Score general      : {result.get('score_general'):.1f}%")
    print(f"Reporte en         : {result.get('reporte_path')}")


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or not os.getenv("ELEVENLABS_API_KEY"),
    reason="Faltan OPENAI_API_KEY o ELEVENLABS_API_KEY",
)
def test_e2e_elevenlabs_branch_only(client: TestClient) -> None:
    """Branch SIN descubrimiento de n8n: solo el agente bajo el branch."""
    resp = client.post(
        "/api/v1/evaluate/elevenlabs",
        json={
            "target_id": ELEVEN_BRANCH_ID,
            "include_n8n_flows": False,
            "total_conversaciones": 0,
        },
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    print(f"\n=== eleven BRANCH solo job: {job_id} ===")

    final = _poll_until_done(client, job_id, timeout_s=300)
    if final["status"] == "failed":
        pytest.fail(f"Job falló:\n{final.get('error', '(sin error)')}")
    result = final["result"]
    assert result["kind"] == "elevenlabs_branch"
    assert result["branch"]["branch_id"] == ELEVEN_BRANCH_ID
    assert "nodos" not in result  # NO es pipeline, es nodo único
    print(f"\nNombre        : {result['nombre']}")
    print(f"Branch        : {result['branch']['branch_name']}")
    print(f"Score general : {result['score_general']:.1f}%")
    print(f"Problemas     : {len(result.get('problemas', []))}")


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or not os.getenv("ELEVENLABS_API_KEY"),
    reason="Faltan OPENAI_API_KEY o ELEVENLABS_API_KEY",
)
def test_e2e_elevenlabs_real(client: TestClient) -> None:
    """Lanza una evaluación real del agente ElevenLabs del usuario."""
    resp = client.post(
        "/api/v1/evaluate/elevenlabs",
        json={
            "agent_id": ELEVEN_AGENT_ID,
            "total_conversaciones": 0,  # solo estático + GPT
        },
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    print(f"\n=== eleven real job: {job_id} ===")

    final = _poll_until_done(client, job_id, timeout_s=300)
    print(f"\n=== FINAL status: {final['status']} ===")

    if final["status"] == "failed":
        pytest.fail(f"Job falló:\n{final.get('error', '(sin error)')}")

    assert final["status"] == "completed"
    result = final["result"]
    assert result["kind"] == "elevenlabs"
    assert "score_general" in result
    assert "reporte_txt" in result
    assert len(result["reporte_txt"]) > 100

    print(f"\nNombre: {result['nombre']}")
    print(f"Agent ID: {result['agent_id']}")
    print(f"Score general: {result['score_general']:.1f}%")
    print(f"Problemas detectados: {len(result.get('problemas', []))}")
    print(f"Reporte guardado en: {result['reporte_path']}")
