import httpx2
import pytest

from app.adapters.http_fetch import FetchError, HttpFetcher


async def public(host: str, port: int) -> list[str]:
    return ["93.184.216.34"]


async def private(host: str, port: int) -> list[str]:
    return ["10.0.0.5"]


def fetcher(handler, **kwargs: object) -> HttpFetcher:  # type: ignore[no-untyped-def]
    return HttpFetcher(transport=httpx2.MockTransport(handler), resolver=public, **kwargs)  # type: ignore[arg-type]


async def test_fetch_follows_safe_redirect() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/a":
            return httpx2.Response(302, headers={"location": "/b"})
        return httpx2.Response(200, content=b"BEGIN:VCALENDAR")

    assert await fetcher(handler).fetch("https://cal.exemplo/a") == b"BEGIN:VCALENDAR"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://x/a", "recusado"),
        ("https://x:8443/a", "recusado"),
        ("file:///etc/passwd", "recusado"),
    ],
)
async def test_refuses_unsafe_urls(url: str, message: str) -> None:
    with pytest.raises(FetchError, match=message):
        await fetcher(lambda r: httpx2.Response(200)).fetch(url)


async def test_refuses_private_address_and_redirect_to_it() -> None:
    with pytest.raises(FetchError, match="não público"):
        await HttpFetcher(
            transport=httpx2.MockTransport(lambda r: httpx2.Response(200)), resolver=private
        ).fetch("https://interno/")

    calls = {"n": 0}

    async def flip(host: str, port: int) -> list[str]:
        calls["n"] += 1
        return ["93.184.216.34"] if calls["n"] == 1 else ["127.0.0.1"]

    redirect = HttpFetcher(
        transport=httpx2.MockTransport(
            lambda r: httpx2.Response(302, headers={"location": "https://local/"})
        ),
        resolver=flip,
    )
    with pytest.raises(FetchError, match="não público"):
        await redirect.fetch("https://cal.exemplo/a")


async def test_errors_and_size_limit() -> None:
    with pytest.raises(FetchError, match="erro 404"):
        await fetcher(lambda r: httpx2.Response(404)).fetch("https://x/a")
    with pytest.raises(FetchError, match="grande demais"):
        await fetcher(lambda r: httpx2.Response(200, content=b"x" * 50), max_bytes=10).fetch(
            "https://x/a"
        )
