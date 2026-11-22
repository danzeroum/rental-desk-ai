import httpx2
import pytest

from app.adapters.link_checker import FakeLinkChecker, HttpLinkChecker, LinkStatus, Resolver

PUBLIC = "93.184.215.14"


def resolver(mapping: dict[str, list[str]]) -> Resolver:
    async def resolve(host: str, port: int) -> list[str]:
        return mapping.get(host, [PUBLIC])

    return resolve


def transport(routes: dict[str, httpx2.Response]) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return routes.get(str(request.url), httpx2.Response(404))

    return httpx2.MockTransport(handler)


async def check(
    url: str, routes: dict[str, httpx2.Response], hosts: dict[str, list[str]] | None = None
) -> LinkStatus:
    checker = HttpLinkChecker(transport=transport(routes), resolver=resolver(hosts or {}))
    return (await checker.check([url]))[url]


async def test_ok_link() -> None:
    status = await check("https://fotos.com/a", {"https://fotos.com/a": httpx2.Response(200)})
    assert status.ok


async def test_not_found() -> None:
    status = await check("https://fotos.com/a", {})
    assert not status.ok
    assert "404" in status.detail


async def test_follows_safe_redirect() -> None:
    routes = {
        "https://fotos.com/a": httpx2.Response(302, headers={"location": "/b"}),
        "https://fotos.com/b": httpx2.Response(200),
    }
    assert (await check("https://fotos.com/a", routes)).ok


async def test_falls_back_to_get_when_head_not_allowed() -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.method)
        return httpx2.Response(405 if request.method == "HEAD" else 200)

    checker = HttpLinkChecker(transport=httpx2.MockTransport(handler), resolver=resolver({}))
    status = (await checker.check(["https://fotos.com/a"]))["https://fotos.com/a"]
    assert status.ok
    assert calls == ["HEAD", "GET"]


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1"]
)
async def test_refuses_private_destinations(address: str) -> None:
    status = await check(
        "http://interno.local/x", {"http://interno.local/x": httpx2.Response(200)},
        {"interno.local": [address]},
    )  # fmt: skip
    assert not status.ok
    assert "não público" in status.detail


async def test_refuses_redirect_to_private_destination() -> None:
    routes = {"https://fotos.com/a": httpx2.Response(302, headers={"location": "http://meta/"})}
    status = await check("https://fotos.com/a", routes, {"meta": ["169.254.169.254"]})
    assert not status.ok


async def test_refuses_non_standard_port() -> None:
    status = await check("https://fotos.com:8443/a", {})
    assert "porta" in status.detail


async def test_too_many_redirects() -> None:
    routes = {"https://fotos.com/a": httpx2.Response(302, headers={"location": "/a"})}
    status = await check("https://fotos.com/a", routes)
    assert status.detail == "redirecionamentos demais"


async def test_network_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("recusado", request=request)

    checker = HttpLinkChecker(transport=httpx2.MockTransport(handler), resolver=resolver({}))
    status = (await checker.check(["https://fotos.com/a"]))["https://fotos.com/a"]
    assert (status.ok, status.detail) == (False, "não foi possível acessar")


async def test_timeout() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("lento", request=request)

    checker = HttpLinkChecker(transport=httpx2.MockTransport(handler), resolver=resolver({}))
    status = (await checker.check(["https://fotos.com/a"]))["https://fotos.com/a"]
    assert status.detail == "não respondeu a tempo"


async def test_duplicates_checked_once() -> None:
    hits: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        hits.append(str(request.url))
        return httpx2.Response(200)

    checker = HttpLinkChecker(transport=httpx2.MockTransport(handler), resolver=resolver({}))
    result = await checker.check(["https://fotos.com/a", "https://fotos.com/a"])
    assert list(result) == ["https://fotos.com/a"]
    assert hits == ["https://fotos.com/a"]


async def test_fake_checker() -> None:
    fake = FakeLinkChecker(broken=["https://x/quebrado"])
    result = await fake.check(["https://x/ok", "https://x/quebrado"])
    assert result["https://x/ok"].ok
    assert not result["https://x/quebrado"].ok
