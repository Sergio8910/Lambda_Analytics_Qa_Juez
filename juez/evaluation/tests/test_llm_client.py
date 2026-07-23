"""El selector de proveedor LLM (juez/llm_client.py): por defecto OpenAI, y en
modo Claude un adaptador sobre el SDK oficial `anthropic` que expone la misma
superficie que OpenAI (chat.completions -> choices[0].message)."""
from __future__ import annotations

import json

import juez.llm_client as llm


def test_provider_default_es_openai(monkeypatch):
    monkeypatch.delenv("JUEZ_LLM_PROVIDER", raising=False)
    assert llm.usando_claude() is False


def test_provider_anthropic_o_claude(monkeypatch):
    monkeypatch.setenv("JUEZ_LLM_PROVIDER", "anthropic")
    assert llm.usando_claude() is True
    monkeypatch.setenv("JUEZ_LLM_PROVIDER", "claude")
    assert llm.usando_claude() is True


def test_provider_ordo(monkeypatch):
    monkeypatch.setenv("JUEZ_LLM_PROVIDER", "ordo")
    assert llm.usando_ordo() is True
    assert llm.usando_claude() is False


def test_api_key_presente_por_proveedor(monkeypatch):
    monkeypatch.setenv("JUEZ_LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.api_key_presente() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert llm.api_key_presente() is True


def test_api_key_presente_ordo(monkeypatch):
    monkeypatch.setenv("JUEZ_LLM_PROVIDER", "ordo")
    monkeypatch.delenv("ORDO_API_KEY", raising=False)
    assert llm.api_key_presente() is False
    monkeypatch.setenv("ORDO_API_KEY", "ordo_sk_test")
    assert llm.api_key_presente() is True


def test_mapear_modelo(monkeypatch):
    monkeypatch.delenv("CLAUDE_JUDGE_MODEL", raising=False)
    assert llm.mapear_modelo("gpt-4o-mini") == "claude-opus-4-8"   # gpt-* remapeado
    assert llm.mapear_modelo("claude-sonnet-5") == "claude-sonnet-5"  # claude-* intacto
    monkeypatch.setenv("CLAUDE_JUDGE_MODEL", "claude-haiku-4-5")
    assert llm.mapear_modelo("gpt-4o") == "claude-haiku-4-5"       # override por env


def test_separar_system_extrae_system_y_convierte_tool_roles():
    system, msgs = llm.separar_system([
        {"role": "system", "content": "reglas"},
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "function": {"name": "buscar", "arguments": '{"q": "x"}'}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"ok": true}'},
    ])
    assert system == "reglas"
    assert msgs[0] == {"role": "user", "content": "hola"}
    # assistant con tool_calls -> bloque tool_use con input parseado
    assert msgs[1]["role"] == "assistant"
    tu = msgs[1]["content"][0]
    assert tu["type"] == "tool_use" and tu["name"] == "buscar" and tu["input"] == {"q": "x"}
    # rol tool -> mensaje user con tool_result
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["tool_use_id"] == "call_1"


def test_tools_y_tool_choice_a_anthropic():
    tools = [{"type": "function", "function": {
        "name": "clima", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
    out = llm.tools_a_anthropic(tools)
    assert out == [{"name": "clima", "description": "d",
                    "input_schema": {"type": "object", "properties": {}}}]
    assert llm.tool_choice_a_anthropic("auto") is None
    assert llm.tool_choice_a_anthropic("required") == {"type": "any"}
    assert llm.tool_choice_a_anthropic(
        {"type": "function", "function": {"name": "clima"}}) == {"type": "tool", "name": "clima"}


class _Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeAnthResp:
    def __init__(self, blocks, stop="end_turn"):
        self.content = blocks
        self.stop_reason = stop
        self.model = "claude-opus-4-8"


class _FakeAnth:
    """Simula anthropic.Anthropic: captura kwargs y devuelve una respuesta fija."""
    def __init__(self, resp):
        self._resp = resp
        self.messages = self
        self.captured = None

    def create(self, **kw):
        self.captured = kw
        return self._resp


def test_adapter_texto_forma_openai():
    fake = _FakeAnth(_FakeAnthResp([_Block(type="text", text='{"score": 9}')]))
    c = llm._AnthropicChatClient(client=fake)
    out = c.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": "juez"}, {"role": "user", "content": "eval"}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    # request traducido correctamente
    assert fake.captured["model"] == "claude-opus-4-8"
    assert fake.captured["system"].startswith("juez")
    assert "Responde ÚNICAMENTE con JSON" in fake.captured["system"]  # response_format -> nota
    assert "temperature" not in fake.captured                         # sampling descartado
    assert fake.captured["max_tokens"] == 4096                        # default
    # respuesta con forma de OpenAI
    assert out.choices[0].message.content == '{"score": 9}'
    assert out.choices[0].message.tool_calls is None
    assert json.loads(out.choices[0].message.content) == {"score": 9}


def test_adapter_tool_calls_forma_openai():
    blocks = [_Block(type="tool_use", id="tu_1", name="buscar", input={"q": "x"})]
    fake = _FakeAnth(_FakeAnthResp(blocks, stop="tool_use"))
    c = llm._AnthropicChatClient(client=fake)
    out = c.chat.completions.create(
        model="gpt-4o", max_tokens=600,
        messages=[{"role": "user", "content": "busca x"}],
        tools=[{"type": "function", "function": {"name": "buscar", "parameters": {"type": "object", "properties": {}}}}],
        tool_choice="auto",
    )
    assert fake.captured["max_tokens"] == 600
    assert fake.captured["tools"][0]["name"] == "buscar"
    tc = out.choices[0].message.tool_calls[0]
    assert tc.id == "tu_1"
    assert tc.function.name == "buscar"
    assert json.loads(tc.function.arguments) == {"q": "x"}


def test_make_chat_client_default_es_openai(monkeypatch):
    """Sin flag, make_chat_client devuelve el cliente real de OpenAI (no rompe nada)."""
    monkeypatch.delenv("JUEZ_LLM_PROVIDER", raising=False)
    from openai import OpenAI
    c = llm.make_chat_client(api_key="sk-test")
    assert isinstance(c, OpenAI)


class _FakeHttpResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _FakeOrdoSession:
    def __init__(self):
        self.posts = []
        self.gets = []
        self._polls = [
            _FakeHttpResponse(200, {"status": "running", "done": False}),
            _FakeHttpResponse(
                200,
                {
                    "status": "success",
                    "done": True,
                    "reply": '```json\n{"score": 91}\n```',
                },
            ),
        ]

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _FakeHttpResponse(
            202,
            {"conversation_id": "conv_test", "status": "running"},
        )

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return self._polls.pop(0)


def test_adapter_ordo_post_poll_y_forma_openai(monkeypatch):
    monkeypatch.setenv("ORDO_POLL_INTERVAL_S", "0")
    fake = _FakeOrdoSession()
    client = llm._OrdoChatClient(
        api_key="ordo_sk_test",
        base_url="https://ordo.test/",
        session=fake,
        max_retries=0,
    )

    result = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Evalúa con rigor."},
            {"role": "user", "content": "Devuelve el score."},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    assert result.choices[0].message.content == '{"score": 91}'
    assert result.model == "ordo"
    assert len(fake.posts) == 1
    post_url, post_kwargs = fake.posts[0]
    assert post_url == "https://ordo.test/api/v1/chat/"
    assert post_kwargs["headers"]["X-Api-Key"] == "ordo_sk_test"
    assert set(post_kwargs["json"]) == {"message"}
    prompt = post_kwargs["json"]["message"]
    assert "Solicitud informativa de solo lectura" in prompt
    assert "Conserva tu identidad y tus reglas como Ordo" in prompt
    assert "Evalúa con rigor." in prompt
    assert "Devuelve únicamente JSON válido" in prompt
    assert fake.gets[-1][0].endswith("/api/v1/chat/conv_test/")


def test_make_chat_client_ordo_ignora_api_key_openai(monkeypatch):
    monkeypatch.setenv("JUEZ_LLM_PROVIDER", "ordo")
    monkeypatch.setenv("ORDO_API_KEY", "ordo_sk_correcta")
    client = llm.make_chat_client(api_key="sk-openai-no-usar")
    headers = client.chat.completions._headers()
    assert headers["X-Api-Key"] == "ordo_sk_correcta"
