"""Transcrição de áudio por API (SPEC, seção 0b). O áudio só existe em memória.

`OpenAICompatibleTranscriber` fala o formato `POST /audio/transcriptions`
(multipart: `file`, `model`, `language`), aceito por vários provedores. O
provedor deve ser contratado sem uso dos dados para treino (pendência humana).
"""

from __future__ import annotations

from typing import Final, Protocol

import httpx2

from app.core.logging import get_logger

TIMEOUT_SECONDS: Final = 30.0
MAX_TRANSCRIPT_CHARS: Final = 4000
EXTENSIONS: Final = {
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
}

log = get_logger(__name__)


class TranscriptionError(Exception):
    pass


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, mime_type: str) -> str: ...


class OpenAICompatibleTranscriber:
    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        model: str,
        language: str = "pt",
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._url, self._api_key, self._model, self._language = url, api_key, model, language
        self._transport = transport

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        base_mime = mime_type.split(";", maxsplit=1)[0].strip()
        filename = f"audio.{EXTENSIONS.get(base_mime, 'ogg')}"
        try:
            async with httpx2.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data={
                        "model": self._model,
                        "language": self._language,
                        "response_format": "json",
                    },
                    files={"file": (filename, audio, base_mime)},
                )
        except httpx2.HTTPError as exc:
            raise TranscriptionError(f"falha de rede: {type(exc).__name__}") from exc
        if response.is_error:
            log.warning("transcricao_erro", status=response.status_code)
            raise TranscriptionError(f"provedor respondeu {response.status_code}")
        try:
            text = str(response.json()["text"]).strip()
        except (ValueError, KeyError, TypeError):
            raise TranscriptionError("resposta do provedor sem texto") from None
        if not text:
            raise TranscriptionError("áudio sem fala reconhecida")
        return text[:MAX_TRANSCRIPT_CHARS]


class FakeTranscriber:
    def __init__(
        self, text: str = "Quero uma casa para 6 pessoas no Réveillon", *, fail: bool = False
    ) -> None:
        self.text, self.fail = text, fail
        self.calls = 0

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        self.calls += 1
        if self.fail:
            raise TranscriptionError("falha simulada")
        return self.text
