from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import juez.colmena.ai_swarm as swarm
import juez.llm_client as llm
from juez.swarm_context import SwarmContextRegistry, activate_bee, current_bee_context


def test_catalogo_define_especialistas_de_ia_unicos():
    ids = [spec.bee_id for spec in swarm._SPECS]
    assert len(ids) == 13
    assert len(ids) == len(set(ids))
    assert {"guardiana_seguridad", "exploradora", "ninera", "rendimiento"} <= set(ids)


class _FakeCompletionClient:
    def __init__(self, captured):
        self.captured = captured
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        context = current_bee_context()
        bee_id = context.bee_id if context else "none"
        self.captured.append((bee_id, kwargs["messages"]))
        if bee_id == "reina":
            data = {
                "summary": "Dos especialistas colaboraron.",
                "decision": "observations",
                "consensus_findings": [{
                    "severity": "medium",
                    "category": "security",
                    "title": "Hallazgo consensuado",
                    "evidence": "Coincidencia de especialistas",
                    "recommendation": "Revisar",
                    "supporting_bees": ["seguridad", "api"],
                    "confidence": 0.9,
                }],
                "conflicts": [],
                "coverage_gaps": [],
                "priorities": ["Hallazgo consensuado"],
            }
        else:
            data = {
                "bee_id": bee_id,
                "status": "completed",
                "summary": f"Informe de {bee_id}",
                "confidence": 0.9,
                "findings": [{
                    "severity": "medium",
                    "category": "security" if bee_id == "seguridad" else "api",
                    "title": f"Hallazgo de {bee_id}",
                    "evidence": "evidencia",
                    "recommendation": "recomendación",
                }],
                "dependencies": [],
                "questions_for_queen": [],
            }
        message = SimpleNamespace(content=json.dumps(data, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_especialistas_publican_y_reina_consolida(monkeypatch):
    specs = (
        swarm.AIWorkerSpec("seguridad", "Seguridad", "Revisar seguridad", ("security",)),
        swarm.AIWorkerSpec("api", "API", "Revisar contratos", ("api",)),
    )
    monkeypatch.setattr(swarm, "_SPECS", specs)
    monkeypatch.setattr(swarm, "_source_context", lambda *args: [])
    captured = []
    monkeypatch.setattr(
        swarm,
        "make_chat_client",
        lambda **kwargs: _FakeCompletionClient(captured),
    )
    inventory = SimpleNamespace(
        frameworks=["fastapi"],
        languages=["python"],
        detected_assets={"apis": 1},
        assets=[],
    )
    registry = SwarmContextRegistry(
        "eval-test",
        context_limit_tokens=100_000,
        per_bee_budget_tokens=100_000,
        global_budget_tokens=500_000,
    )

    result = swarm.run_ai_swarm(
        project_id="demo",
        root=Path(".").resolve(),
        inventory=inventory,
        deterministic_findings=[],
        registry=registry,
    )

    assert [r["bee_id"] for r in result["specialists"]] == ["api", "seguridad"]
    assert result["queen"]["decision"] == "observations"
    assert result["queen"]["consensus_findings"][0]["supporting_bees"] == ["seguridad", "api"]
    context_report = result["context_registry"]
    assert context_report["bee_count"] == 3
    assert {b["bee_id"] for b in context_report["queen_digest"]} == {"api", "seguridad", "reina"}
    queen_messages = next(messages for bee, messages in captured if bee == "reina")
    queen_board = queen_messages[-1]["content"]
    assert '"bee_id": "api"' in queen_board
    assert '"bee_id": "seguridad"' in queen_board


class _Response:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class _OrdoSession:
    def __init__(self):
        self.posts = []
        self.counter = 0

    def post(self, url, **kwargs):
        self.posts.append(kwargs["json"])
        self.counter += 1
        existing = kwargs["json"].get("conversation_id")
        return _Response({"conversation_id": existing or f"conv-{self.counter}", "status": "running"})

    def get(self, url, **kwargs):
        return _Response({"status": "success", "done": True, "reply": "ok"})


def test_ordo_reusa_contexto_de_una_abeja_y_aisla_otra(monkeypatch):
    monkeypatch.setenv("ORDO_POLL_INTERVAL_S", "0")
    session = _OrdoSession()
    client = llm._OrdoChatClient(
        api_key="ordo_sk_test",
        base_url="https://ordo.test",
        session=session,
        max_retries=0,
    )
    registry = SwarmContextRegistry(
        "eval-context",
        context_limit_tokens=100_000,
        per_bee_budget_tokens=100_000,
        global_budget_tokens=500_000,
    )

    with activate_bee(registry, bee_id="seguridad", role="Seguridad", component="demo"):
        client.chat.completions.create(model="juez", messages=[{"role": "user", "content": "uno"}])
        client.chat.completions.create(model="juez", messages=[{"role": "user", "content": "dos"}])
    with activate_bee(registry, bee_id="api", role="API", component="demo"):
        client.chat.completions.create(model="juez", messages=[{"role": "user", "content": "otro"}])

    assert "conversation_id" not in session.posts[0]
    assert session.posts[1]["conversation_id"] == "conv-1"
    assert "conversation_id" not in session.posts[2]
