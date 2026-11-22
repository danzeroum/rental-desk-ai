import json
from typing import Any

import anthropic
import httpx2
import pytest

from app.adapters.fake_llm import FakeLLM
from app.adapters.llm import AnthropicLLMClient, LLMUnavailableError, loads_object
from app.domain.prompts import EXTRACTION_SCHEMA, WRITER_SCHEMA

SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def message(text: str = '{"ok": true}', stop: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}], "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }  # fmt: skip


def client(handler: Any, model: str = "claude-opus-5-5", **kwargs: Any) -> AnthropicLLMClient:
    sdk = anthropic.AsyncAnthropic(
        api_key="k",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicLLMClient(model=model, client=sdk, **kwargs)


async def test_sends_schema_effort_and_refusal_fallback() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=message())

    text = await client(handler).complete_json(system="s", user="u", schema=SCHEMA)
    assert json.loads(text) == {"ok": True}
    body = json.loads(seen[0].content)
    assert body["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": SCHEMA},
    }
    assert body["system"] == "s"
    assert body["messages"] == [{"role": "user", "content": "u"}]
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen[0].headers["anthropic-beta"]


async def test_no_fallback_for_models_without_support() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=message())

    await client(handler, model="claude-haiku-5-5").complete_json(
        system="s", user="u", schema=SCHEMA
    )
    assert "fallbacks" not in json.loads(seen[0].content)


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
async def test_unusable_stop_reason(stop: str) -> None:
    with pytest.raises(LLMUnavailableError, match=stop):
        await client(lambda r: httpx2.Response(200, json=message(stop=stop))).complete_json(
            system="s", user="u", schema=SCHEMA
        )


@pytest.mark.parametrize("status", [400, 401, 429, 500, 529])
async def test_api_errors_become_unavailable(status: int) -> None:
    error = {"type": "error", "error": {"type": "api_error", "message": "x"}}
    with pytest.raises(LLMUnavailableError, match=str(status)):
        await client(lambda r: httpx2.Response(status, json=error)).complete_json(
            system="s", user="u", schema=SCHEMA
        )


async def test_connection_error_becomes_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("caiu", request=request)

    with pytest.raises(LLMUnavailableError, match="conexão"):
        await client(handler).complete_json(system="s", user="u", schema=SCHEMA)


def test_loads_object() -> None:
    assert loads_object('{"a": 1}') == {"a": 1}
    assert loads_object("[1]") is None
    assert loads_object("não é json") is None


async def test_fake_llm_extraction_and_writer() -> None:
    fake = FakeLLM()
    raw = await fake.complete_json(
        system="",
        user="Hoje: 2026-11-21\nhóspede (última): casa para 12 no réveillon",
        schema=EXTRACTION_SCHEMA,
    )
    data = json.loads(raw)
    assert data["slots_update"]["guests"] == 12
    assert data["slots_update"]["check_in"] == "2026-12-28"
    writer = json.loads(await fake.complete_json(system="", user="", schema=WRITER_SCHEMA))
    assert set(writer) == {"abertura", "fechamento"}
    with pytest.raises(LLMUnavailableError):
        await FakeLLM(fail=True).complete_json(system="", user="", schema=SCHEMA)
