from __future__ import annotations

from juez.evaluation.normalize import normalize_agent_result


def test_normalize_string() -> None:
    env = normalize_agent_result("hola")
    assert env.output_text == "hola"
    assert isinstance(env.retrieval_context, list)


def test_normalize_dict_response() -> None:
    env = normalize_agent_result({"response": "ok"})
    assert env.output_text == "ok"
    assert isinstance(env.retrieval_context, list)


def test_normalize_dict_text_context() -> None:
    env = normalize_agent_result({"text": "texto", "context": ["c1", "c2"]})
    assert env.output_text == "texto"
    assert env.retrieval_context == ["c1", "c2"]


def test_normalize_tool_calls() -> None:
    env = normalize_agent_result({"response": "ok", "tool_calls": [{"name": "buscar"}]})
    assert env.output_text == "ok"
    assert isinstance(env.tool_calls, list)
    assert len(env.tool_calls) == 1


def test_normalize_object_dict() -> None:
    class Obj:
        def __init__(self):
            self.response = "ok"
            self.context = ["ctx"]

    env = normalize_agent_result(Obj())
    assert env.output_text == "ok"
    assert env.retrieval_context == ["ctx"]


def test_normalize_generator() -> None:
    def gen():
        yield "a"
        yield "b"
    env = normalize_agent_result(gen())
    assert env.output_text == "ab"


def test_normalize_empty() -> None:
    env = normalize_agent_result({})
    assert env.output_text == ""
    assert isinstance(env.retrieval_context, list)


def test_normalize_int() -> None:
    env = normalize_agent_result(123)
    assert env.output_text == "123"
    assert isinstance(env.retrieval_context, list)
