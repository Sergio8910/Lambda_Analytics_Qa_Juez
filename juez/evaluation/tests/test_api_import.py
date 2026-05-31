from __future__ import annotations


def test_api_server_importable():
    from juez.evaluation.api import server

    assert hasattr(server, "app")
