import json
from urllib.parse import parse_qs

import httpx2
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.adapters.google_sheet import GoogleSheetSource, SheetError, sheet_id_from

SHEET = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abc"


def account() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return json.dumps({"client_email": "sa@proj.iam.gserviceaccount.com", "private_key": pem,
                       "token_uri": "https://oauth2.exemplo/token"})  # fmt: skip


def test_sheet_id_from_link() -> None:
    assert sheet_id_from(f"https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=0") == SHEET
    assert sheet_id_from(SHEET) == SHEET
    with pytest.raises(SheetError):
        sheet_id_from("https://exemplo.com/planilha")


async def test_exports_xlsx_with_cached_token() -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            assert form["assertion"][0].count(".") == 2
            return httpx2.Response(200, json={"access_token": "tok", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer tok"
        assert request.url.params["mimeType"].endswith("spreadsheetml.sheet")
        return httpx2.Response(200, content=b"PK-xlsx")

    source = GoogleSheetSource(account(), transport=httpx2.MockTransport(handler))
    assert await source.fetch_xlsx(SHEET) == b"PK-xlsx"
    assert await source.fetch_xlsx(SHEET) == b"PK-xlsx"
    assert calls.count("/token") == 1


async def test_not_shared_and_invalid_account() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/token":
            return httpx2.Response(200, json={"access_token": "tok"})
        return httpx2.Response(404)

    source = GoogleSheetSource(account(), transport=httpx2.MockTransport(handler))
    with pytest.raises(SheetError, match="compartilhada com sa@proj"):
        await source.fetch_xlsx(SHEET)
    with pytest.raises(SheetError, match="inválido"):
        GoogleSheetSource("{}")
