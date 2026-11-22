"""Leitura de conteúdo de endereço público (calendários iCal, RF26), com limites.

Mesma proteção do verificador de links: só http(s), porta padrão, endereço público,
conferido a cada redirecionamento. Tamanho e tempo limitados.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Protocol
from urllib.parse import urljoin

import httpx2

from app.adapters.link_checker import (
    USER_AGENT,
    Resolver,
    UnsafeDestinationError,
    _system_resolver,
    ensure_public_url,
)

MAX_BYTES: Final = 2 * 1024 * 1024
TIMEOUT_SECONDS: Final = 15.0
MAX_REDIRECTS: Final = 3


class FetchError(Exception):
    """Mensagem curta para o painel (sem o endereço, que pode ter token)."""


class Fetcher(Protocol):
    async def fetch(self, url: str) -> bytes: ...


class HttpFetcher:
    def __init__(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        resolver: Resolver = _system_resolver,
        max_bytes: int = MAX_BYTES,
    ) -> None:
        self._transport, self._resolve, self.max_bytes = transport, resolver, max_bytes

    async def fetch(self, url: str) -> bytes:
        current = url
        try:
            async with httpx2.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS, follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
            ) as client:  # fmt: skip
                for _ in range(MAX_REDIRECTS + 1):
                    await ensure_public_url(current, self._resolve)
                    async with client.stream("GET", current) as response:
                        if response.is_redirect and "location" in response.headers:
                            current = urljoin(current, response.headers["location"])
                            continue
                        if response.is_error:
                            raise FetchError(
                                f"o endereço respondeu com erro {response.status_code}"
                            )
                        return await self._read(response)
                raise FetchError("redirecionamentos demais")
        except UnsafeDestinationError as exc:
            raise FetchError(f"endereço recusado: {exc}") from exc
        except (httpx2.TimeoutException, TimeoutError) as exc:
            raise FetchError("o endereço não respondeu a tempo") from exc
        except (httpx2.HTTPError, OSError) as exc:
            raise FetchError("não foi possível acessar o endereço") from exc

    async def _read(self, response: httpx2.Response) -> bytes:
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self.max_bytes:
                raise FetchError("conteúdo grande demais (mais de 2 MB)")
            chunks.append(chunk)
        return b"".join(chunks)


@dataclass
class FakeFetcher:
    """Para testes e para o modo demonstração."""

    pages: dict[str, bytes | Exception] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    async def fetch(self, url: str) -> bytes:
        self.calls.append(url)
        page = self.pages.get(url, FetchError("o endereço respondeu com erro 404"))
        if isinstance(page, Exception):
            raise page
        return page
