import hashlib
import hmac
from typing import Any

from app.adapters.whatsapp_webhook import parse_webhook, signature_is_valid
from app.models.enums import MessageKind


def payload(
    *messages: dict[str, Any], field: str = "messages", key: str = "messages"
) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": field,
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "551130000000",
                                "phone_number_id": "PNID",
                            },
                            "contacts": [{"profile": {"name": "Ana"}, "wa_id": "5511999990000"}],
                            key: list(messages),
                        },
                    }
                ],
            }
        ],
    }


def text_msg(mid: str = "wamid.1", body: str = "Oi") -> dict[str, Any]:
    return {
        "from": "5511999990000",
        "id": mid,
        "timestamp": "1795300000",
        "type": "text",
        "text": {"body": body},
    }


class TestSignature:
    body = b'{"a":1}'

    def sign(self, secret: str) -> str:
        return "sha256=" + hmac.new(secret.encode(), self.body, hashlib.sha256).hexdigest()

    def test_valid(self) -> None:
        assert signature_is_valid("segredo", self.body, self.sign("segredo"))

    def test_invalid(self) -> None:
        assert not signature_is_valid("segredo", self.body, self.sign("outro"))
        assert not signature_is_valid("segredo", self.body + b" ", self.sign("segredo"))
        assert not signature_is_valid("segredo", self.body, None)
        assert not signature_is_valid("", self.body, self.sign(""))


def test_text_message() -> None:
    batch = parse_webhook(payload(text_msg()))
    (msg,) = batch.messages
    assert (msg.phone_number_id, msg.external_id, msg.wa_id, msg.kind, msg.text) == (
        "PNID", "wamid.1", "5511999990000", MessageKind.TEXT, "Oi",
    )  # fmt: skip
    assert msg.profile_name == "Ana"
    assert msg.timestamp.year == 2026


def test_audio_message() -> None:
    audio = {
        "from": "5511999990000", "id": "wamid.2", "timestamp": "1795300000", "type": "audio",
        "audio": {"id": "MEDIA1", "mime_type": "audio/ogg; codecs=opus", "voice": True},
    }  # fmt: skip
    (msg,) = parse_webhook(payload(audio)).messages
    assert (msg.kind, msg.media_id, msg.media_mime, msg.text) == (
        MessageKind.AUDIO, "MEDIA1", "audio/ogg; codecs=opus", None,
    )  # fmt: skip


def test_button_and_interactive_become_text() -> None:
    button = {"from": "5511999990000", "id": "b", "type": "button", "button": {"text": "1"}}
    interactive = {
        "from": "5511999990000", "id": "i", "type": "interactive",
        "interactive": {"type": "button_reply", "button_reply": {"id": "x", "title": "Assumir"}},
    }  # fmt: skip
    texts = [m.text for m in parse_webhook(payload(button, interactive)).messages]
    assert texts == ["1", "Assumir"]


def test_unknown_type_is_other() -> None:
    sticker = {"from": "5511999990000", "id": "s", "type": "sticker", "sticker": {"id": "X"}}
    (msg,) = parse_webhook(payload(sticker)).messages
    assert (msg.kind, msg.text, msg.media_id) == (MessageKind.OTHER, None, None)


def test_malformed_items_are_discarded_individually() -> None:
    junk: Any = "lixo"
    batch = parse_webhook(payload(text_msg(), {"id": "sem-remetente"}, junk, text_msg("wamid.3")))
    assert [m.external_id for m in batch.messages] == ["wamid.1", "wamid.3"]
    assert batch.discarded == 2


def test_statuses_only_payload_has_no_messages() -> None:
    data = payload()
    data["entry"][0]["changes"][0]["value"]["statuses"] = [{"id": "wamid.1", "status": "read"}]
    assert parse_webhook(data).messages == []


def test_echo_from_business_app() -> None:
    echo = {
        "from": "551130000000",
        "to": "5511999990000",
        "id": "wamid.E",
        "timestamp": "1795300000",
        "type": "text",
        "text": {"body": "Oi, aqui é o corretor"},
    }
    batch = parse_webhook(payload(echo, field="smb_message_echoes", key="message_echoes"))
    (item,) = batch.echoes
    assert (item.to_wa_id, item.external_id, item.text) == (
        "5511999990000",
        "wamid.E",
        "Oi, aqui é o corretor",
    )
    assert batch.messages == []


def test_garbage_payloads() -> None:
    garbage_items: list[Any] = [
        None,
        [],
        "x",
        {"entry": "x"},
        {"entry": [{"changes": [{"value": {}}]}]},
    ]
    for garbage in garbage_items:
        batch = parse_webhook(garbage)
        assert (batch.messages, batch.echoes) == ([], [])


def test_long_text_is_truncated() -> None:
    (msg,) = parse_webhook(payload(text_msg(body="a" * 10_000))).messages
    assert msg.text is not None and len(msg.text) == 4096


def test_repr_hides_personal_data() -> None:
    (msg,) = parse_webhook(payload(text_msg(body="meu cpf 123"))).messages
    assert "5511999990000" not in repr(msg)
    assert "cpf" not in repr(msg)


def test_statuses_are_parsed_and_invalid_ones_discarded() -> None:
    payload = {"entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "PN1"},
        "statuses": [
            {"id": "wamid.1", "status": "delivered", "timestamp": "1795543749",
             "recipient_id": "5511999990000"},
            {"id": "wamid.2", "status": "failed", "timestamp": "1795543750",
             "errors": [{"code": 131026, "title": "Message undeliverable"}]},
            {"id": "wamid.3", "status": "estranho"},
        ],
    }}]}]}  # fmt: skip
    batch = parse_webhook(payload)
    assert [(s.external_id, s.status) for s in batch.statuses] == [
        ("wamid.1", "delivered"), ("wamid.2", "failed")]  # fmt: skip
    assert batch.statuses[1].error_code == 131026 and batch.discarded == 1
    assert "5511999990000" not in repr(batch.statuses[0])
