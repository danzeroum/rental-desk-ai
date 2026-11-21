import io
import json
import logging
from collections.abc import Iterator

import pytest

from app.core.logging import bind_context, clear_context, configure_logging, get_logger


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    clear_context()
    yield stream
    clear_context()


def _last_event(stream: io.StringIO) -> dict[str, object]:
    lines = [line for line in stream.getvalue().splitlines() if line]
    event: dict[str, object] = json.loads(lines[-1])
    return event


def test_event_is_json_with_context_keys(log_stream: io.StringIO) -> None:
    get_logger("t").info("mensagem_recebida")
    event = _last_event(log_stream)
    assert event["event"] == "mensagem_recebida"
    assert event["level"] == "info"
    assert {"tenant_id", "conversation_id", "trace_id", "timestamp"} <= event.keys()


def test_bound_context_appears_in_event(log_stream: io.StringIO) -> None:
    bind_context(tenant_id="t-1", trace_id="abc")
    get_logger("t").info("x")
    event = _last_event(log_stream)
    assert event["tenant_id"] == "t-1"
    assert event["trace_id"] == "abc"


def test_unknown_context_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="desconhecidas"):
        bind_context(phone="5511987654321")


def test_sensitive_keys_are_removed(log_stream: io.StringIO) -> None:
    get_logger("t").info("x", phone="5511987654321", text="oi", nested={"name": "Ana"})
    event = _last_event(log_stream)
    assert event["phone"] == "[REMOVIDO]"
    assert event["text"] == "[REMOVIDO]"
    assert event["nested"] == {"name": "[REMOVIDO]"}


def test_free_text_is_masked(log_stream: io.StringIO) -> None:
    get_logger("t").warning("falha ao processar cpf 123.456.789-09", items=["a@b.co"])
    event = _last_event(log_stream)
    assert event["event"] == "falha ao processar cpf [CPF]"
    assert event["items"] == ["[EMAIL]"]


def test_stdlib_loggers_also_emit_masked_json(log_stream: io.StringIO) -> None:
    logging.getLogger("terceiro").error("contato a@b.co")
    event = _last_event(log_stream)
    assert event["event"] == "contato [EMAIL]"
    assert event["tenant_id"] is None


def test_uvicorn_access_logger_is_disabled(log_stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.access").info("GET /")
    assert "GET /" not in log_stream.getvalue()
