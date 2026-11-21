"""Checagem dos links de fotos (aviso não bloqueante do RF14).

O link vem do gestor, então a checagem se protege contra SSRF: só http/https,
portas padrão, nenhum destino em rede privada, de loopback ou reservada, e cada
redirecionamento é conferido de novo. O resultado é só "no ar / fora do ar".
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol
from urllib.parse import urljoin, urlsplit

import httpx2

from app.core.logging import get_logger

MAX_REDIRECTS: Final = 3
TIMEOUT_SECONDS: Final = 5.0
CONCURRENCY: Final = 5
USER_AGENT: Final = "rental-desk-ai-link-check/1.0"

log = get_logger(__name__)

Resolver = Callable[[str, int], Awaitable[list[str]]]


@dataclass(frozen=True)
class LinkStatus:
    ok: bool
    detail: str


class LinkChecker(Protocol):
    async def check(self, urls: Sequence[str]) -> dict[str, LinkStatus]: ...


class UnsafeDestinationError(Exception):
    pass


async def _system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast


class HttpLinkChecker:
    def __init__(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        resolver: Resolver = _system_resolver,
    ) -> None:
        self._transport = transport
        self._resolve = resolver

    async def check(self, urls: Sequence[str]) -> dict[str, LinkStatus]:
        unique = list(dict.fromkeys(urls))
        semaphore = asyncio.Semaphore(CONCURRENCY)
        async with httpx2.AsyncClient(
            transport=self._transport,
            timeout=TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        ) as client:

            async def one(url: str) -> tuple[str, LinkStatus]:
                async with semaphore:
                    return url, await self._check_one(client, url)

            results = await asyncio.gather(*(one(url) for url in unique))
        return dict(results)

    async def _ensure_safe(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise UnsafeDestinationError("endereço inválido")
        default_port = 443 if parts.scheme == "https" else 80
        if parts.port not in (None, default_port):
            raise UnsafeDestinationError("porta não permitida")
        addresses = await self._resolve(parts.hostname, default_port)
        if not addresses or not all(_is_public(a) for a in addresses):
            raise UnsafeDestinationError("endereço não público")

    async def _check_one(self, client: httpx2.AsyncClient, url: str) -> LinkStatus:
        current = url
        try:
            for _ in range(MAX_REDIRECTS + 1):
                await self._ensure_safe(current)
                response = await client.head(current)
                if response.status_code in {403, 405, 501}:  # alguns sites não aceitam HEAD
                    async with client.stream("GET", current) as streamed:
                        response = streamed
                if response.is_redirect and "location" in response.headers:
                    current = urljoin(current, response.headers["location"])
                    continue
                if not response.is_error:
                    return LinkStatus(True, "no ar")
                return LinkStatus(False, f"respondeu com erro {response.status_code}")
            return LinkStatus(False, "redirecionamentos demais")
        except UnsafeDestinationError as exc:
            return LinkStatus(False, f"link recusado: {exc}")
        except (httpx2.TimeoutException, TimeoutError):
            return LinkStatus(False, "não respondeu a tempo")
        except (httpx2.HTTPError, OSError) as exc:
            log.info("link_fotos_inacessivel", error_type=type(exc).__name__)
            return LinkStatus(False, "não foi possível acessar")


class FakeLinkChecker:
    """Para testes e para o modo demonstração: considera no ar o que não estiver em `broken`."""

    def __init__(self, broken: Sequence[str] = ()) -> None:
        self.broken = set(broken)
        self.calls: list[list[str]] = []

    async def check(self, urls: Sequence[str]) -> dict[str, LinkStatus]:
        self.calls.append(list(urls))
        return {
            url: LinkStatus(False, "respondeu com erro 404")
            if url in self.broken
            else LinkStatus(True, "no ar")
            for url in urls
        }
