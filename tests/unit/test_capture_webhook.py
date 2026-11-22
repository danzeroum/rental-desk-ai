import hashlib
import hmac

from scripts.capture_webhook import challenge_response, signature_is_valid, webhook_fields


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature() -> None:
    body = b'{"a":1}'
    assert signature_is_valid("segredo", body, _sign("segredo", body))


def test_invalid_or_missing_signature() -> None:
    body = b'{"a":1}'
    assert not signature_is_valid("segredo", body, _sign("outro", body))
    assert not signature_is_valid("segredo", body, None)
    assert not signature_is_valid("segredo", body, "md5=abc")


def test_challenge_requires_matching_token() -> None:
    query = "hub.mode=subscribe&hub.verify_token=tok&hub.challenge=42"
    assert challenge_response(query, "tok") == "42"
    assert challenge_response(query, "errado") is None
    assert challenge_response(query, "") is None
    assert challenge_response("hub.mode=x&hub.verify_token=tok&hub.challenge=1", "tok") is None


def test_webhook_fields() -> None:
    payload = {
        "entry": [
            {"changes": [{"field": "messages"}, {"field": "smb_message_echoes"}]},
            {"changes": [{"value": {}}]},
        ]
    }
    assert webhook_fields(payload) == ["messages", "smb_message_echoes"]
    assert webhook_fields([]) == []
