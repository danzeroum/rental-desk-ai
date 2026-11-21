"""Captura de webhooks para a verificação técnica de coexistência.

Ferramenta descartável, só com a biblioteca padrão. Responde ao desafio de
verificação da Meta (GET), confere a assinatura `X-Hub-Signature-256` quando
`APP_SECRET` é informado e grava cada POST, com horário de chegada, num arquivo
JSON Lines. Use só com números de teste: o arquivo contém telefones.

    VERIFY_TOKEN=um-token APP_SECRET=segredo-do-app \\
        python scripts/capture_webhook.py --port 8080 --out captura.jsonl

Roteiro completo: docs/verificacao-coexistencia.md
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlparse

MAX_BODY_BYTES = 1_000_000


def signature_is_valid(app_secret: str, body: bytes, header: str | None) -> bool:
    """Confere `X-Hub-Signature-256: sha256=<hex>` (HMAC-SHA256 do corpo com o app secret)."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def challenge_response(query: str, verify_token: str) -> str | None:
    """Devolve `hub.challenge` se o modo e o token conferem; senão None."""
    params = parse_qs(query)
    mode = params.get("hub.mode", [""])[0]
    token = params.get("hub.verify_token", [""])[0]
    challenge = params.get("hub.challenge", [""])[0]
    if mode == "subscribe" and verify_token and hmac.compare_digest(token, verify_token):
        return challenge
    return None


def webhook_fields(payload: object) -> list[str]:
    """Lista os campos (`messages`, `smb_message_echoes`...) presentes no payload."""
    fields: list[str] = []
    if not isinstance(payload, dict):
        return fields
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []) if isinstance(entry, dict) else []:
            if isinstance(change, dict) and isinstance(change.get("field"), str):
                fields.append(change["field"])
    return fields


class CaptureHandler(BaseHTTPRequestHandler):
    verify_token: ClassVar[str] = ""
    app_secret: ClassVar[str] = ""
    out_path: ClassVar[Path] = Path("captura.jsonl")

    def do_GET(self) -> None:
        challenge = challenge_response(urlparse(self.path).query, self.verify_token)
        if challenge is None:
            self._reply(HTTPStatus.FORBIDDEN, b"forbidden")
            return
        self._reply(HTTPStatus.OK, challenge.encode())

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            self._reply(HTTPStatus.BAD_REQUEST, b"bad request")
            return
        body = self.rfile.read(length)
        signature_ok = None
        if self.app_secret:
            signature_ok = signature_is_valid(
                self.app_secret, body, self.headers.get("X-Hub-Signature-256")
            )
        try:
            payload: object = json.loads(body)
        except json.JSONDecodeError:
            payload = {"_corpo_nao_json": body.decode(errors="replace")}
        record = {
            "recebido_em": datetime.now(UTC).isoformat(),
            "assinatura_valida": signature_ok,
            "campos": webhook_fields(payload),
            "payload": payload,
        }
        with self.out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"{record['recebido_em']} campos={record['campos']} assinatura={signature_ok}")
        self._reply(HTTPStatus.OK, b"ok")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return  # sem log de acesso: a linha acima já resume cada evento

    def _reply(self, status: HTTPStatus, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--out", type=Path, default=Path("captura.jsonl"))
    args = parser.parse_args(argv)

    verify_token = os.environ.get("VERIFY_TOKEN", "")
    if not verify_token:
        print("Defina VERIFY_TOKEN (o mesmo cadastrado no parceiro/Meta).", file=sys.stderr)
        return 2
    CaptureHandler.verify_token = verify_token
    CaptureHandler.app_secret = os.environ.get("APP_SECRET", "")
    CaptureHandler.out_path = args.out
    server = ThreadingHTTPServer((args.host, args.port), CaptureHandler)
    print(f"Capturando em http://{args.host}:{args.port} -> {args.out}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
