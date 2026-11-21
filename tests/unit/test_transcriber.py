import httpx2
import pytest

from app.adapters.transcriber import (
    FakeTranscriber,
    OpenAICompatibleTranscriber,
    TranscriptionError,
)


def transcriber(handler) -> OpenAICompatibleTranscriber:  # type: ignore[no-untyped-def]
    return OpenAICompatibleTranscriber(
        url="https://stt.exemplo/v1/audio/transcriptions", api_key="k", model="m",
        transport=httpx2.MockTransport(handler),
    )  # fmt: skip


async def test_sends_multipart_and_returns_text() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"text": "  casa para seis pessoas  "})

    text = await transcriber(handler).transcribe(b"OGGDATA", "audio/ogg; codecs=opus")
    assert text == "casa para seis pessoas"
    body = seen[0].content
    assert b'name="model"' in body and b'filename="audio.ogg"' in body and b"OGGDATA" in body
    assert b'name="language"\r\n\r\npt' in body
    assert seen[0].headers["Authorization"] == "Bearer k"


@pytest.mark.parametrize(
    "response",
    [httpx2.Response(500), httpx2.Response(200, json={}), httpx2.Response(200, json={"text": " "})],
)
async def test_errors(response: httpx2.Response) -> None:
    with pytest.raises(TranscriptionError):
        await transcriber(lambda r: response).transcribe(b"x", "audio/ogg")


async def test_network_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("lento", request=request)

    with pytest.raises(TranscriptionError, match="rede"):
        await transcriber(handler).transcribe(b"x", "audio/ogg")


async def test_fake() -> None:
    assert await FakeTranscriber("oi").transcribe(b"", "audio/ogg") == "oi"
    with pytest.raises(TranscriptionError):
        await FakeTranscriber(fail=True).transcribe(b"", "audio/ogg")
