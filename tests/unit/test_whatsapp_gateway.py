import json

import httpx2
import pytest

from app.adapters.whatsapp import (
    MAX_MEDIA_BYTES,
    MAX_TEMPLATE_PARAM,
    Channel,
    CloudApiGateway,
    FakeWhatsAppGateway,
    GatewayError,
    Media,
    template_param,
)

BASE = "https://graph.exemplo/v21.0"
CHANNEL = Channel(phone_number_id="123", access_token="tok-secreto")


async def _no_sleep(_: float) -> None:
    return None


def gateway(handler, **kwargs: object) -> CloudApiGateway:  # type: ignore[no-untyped-def]
    return CloudApiGateway(
        base_url=BASE,
        transport=httpx2.MockTransport(handler),
        sleep=_no_sleep,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_send_text_uses_cloud_api_format_and_auth() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    assert await gateway(handler).send_text(CHANNEL, "5511999990000", "Olá") == "wamid.ABC"
    (request,) = seen
    assert str(request.url) == f"{BASE}/123/messages"
    assert request.headers["Authorization"] == "Bearer tok-secreto"
    body = json.loads(request.content)
    assert body == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "5511999990000",
        "type": "text",
        "text": {"preview_url": False, "body": "Olá"},
    }


async def test_partner_auth_header_without_scheme() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"messages": [{"id": "x"}]})

    gw = gateway(handler, auth_header="D360-API-KEY", auth_scheme="")
    await gw.send_text(CHANNEL, "55", "oi")
    assert seen[0].headers["D360-API-KEY"] == "tok-secreto"
    assert "Authorization" not in seen[0].headers


async def test_retries_on_429_and_5xx_then_succeeds() -> None:
    statuses = iter([429, 503, 200])

    def handler(request: httpx2.Request) -> httpx2.Response:
        status = next(statuses)
        return httpx2.Response(status, json={"messages": [{"id": "ok"}]} if status == 200 else {})

    assert await gateway(handler).send_text(CHANNEL, "55", "oi") == "ok"


async def test_gives_up_after_max_attempts() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(503)

    with pytest.raises(GatewayError) as info:
        await gateway(handler).send_text(CHANNEL, "55", "oi")
    assert info.value.retryable
    assert calls == 3


async def test_client_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(400, json={"error": {"message": "número inválido"}})

    with pytest.raises(GatewayError) as info:
        await gateway(handler).send_text(CHANNEL, "55", "oi")
    assert (info.value.retryable, info.value.status, calls) == (False, 400, 1)


async def test_network_error_is_retryable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("caiu", request=request)

    with pytest.raises(GatewayError, match="rede") as info:
        await gateway(handler).send_text(CHANNEL, "55", "oi")
    assert info.value.retryable


async def test_response_without_id() -> None:
    with pytest.raises(GatewayError, match="sem ID"):
        await gateway(lambda r: httpx2.Response(200, json={})).send_text(CHANNEL, "55", "oi")


async def test_fetch_media_two_steps() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == f"{BASE}/MID":
            return httpx2.Response(
                200, json={"url": "https://cdn.exemplo/a.ogg", "mime_type": "audio/ogg"}
            )
        assert request.headers["Authorization"] == "Bearer tok-secreto"
        return httpx2.Response(200, content=b"OGG")

    media = await gateway(handler).fetch_media(CHANNEL, "MID")
    assert (media.content, media.mime_type) == (b"OGG", "audio/ogg")


@pytest.mark.parametrize(
    "info",
    [{"url": "http://inseguro/a"}, {"url": "https://x/a", "file_size": MAX_MEDIA_BYTES + 1}, {}],
)
async def test_fetch_media_refuses_bad_metadata(info: dict[str, object]) -> None:
    with pytest.raises(GatewayError):
        await gateway(lambda r: httpx2.Response(200, json=info)).fetch_media(CHANNEL, "MID")


def test_channel_repr_hides_token() -> None:
    assert "tok-secreto" not in repr(CHANNEL)


async def test_fake_gateway() -> None:
    fake = FakeWhatsAppGateway(media={"m1": Media(b"x", "audio/ogg")})
    assert await fake.send_text(CHANNEL, "55", "oi") == "wamid.fake.1"
    assert fake.sent[0].body == "oi"
    assert (await fake.fetch_media(CHANNEL, "m1")).content == b"x"
    with pytest.raises(GatewayError):
        await fake.fetch_media(CHANNEL, "nao-existe")


async def test_send_template_payload() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"messages": [{"id": "wamid.T"}]})

    gw = gateway(handler)
    wamid = await gw.send_template(
        CHANNEL, "5511900000000", "passagem_corretor", ["A\nB", "x\ty   z"]
    )
    assert wamid == "wamid.T"
    body = json.loads(seen[0].content)
    assert body["type"] == "template"
    assert body["template"]["name"] == "passagem_corretor"
    assert body["template"]["language"] == {"code": "pt_BR"}
    params = body["template"]["components"][0]["parameters"]
    assert [p["text"] for p in params] == ["A · B", "x y z"]


def test_template_param_is_flattened_and_capped() -> None:
    assert template_param("linha 1\n\n  linha 2") == "linha 1 · linha 2"
    assert len(template_param("x" * 5000)) == MAX_TEMPLATE_PARAM


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"quality_rating": "yellow", "id": "123"}, "YELLOW"),
        ({"id": "123"}, "UNKNOWN"),
        ([], "UNKNOWN"),
    ],
)
async def test_phone_quality(body: object, expected: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == f"{BASE}/123?fields=quality_rating"
        return httpx2.Response(200, json=body)

    assert await gateway(handler).phone_quality(CHANNEL) == expected
