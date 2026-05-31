from __future__ import annotations

from fastapi.testclient import TestClient

from juez.evaluation.api import server


def test_n8n_reviewer_ui_loads():
    client = TestClient(server.app)

    response = client.get("/ui/n8n-reviewer")

    assert response.status_code == 200
    assert "Sube un workflow y deja que el Juez lo diagnostique." in response.text
    assert 'id="review-form"' in response.text
    assert "/v1/n8n/analyze" in response.text
