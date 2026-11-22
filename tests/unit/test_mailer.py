import smtplib
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest

from app.adapters.mailer import FakeMailer, MailError, SmtpMailer


class StubSMTP:
    instances: ClassVar[list["StubSMTP"]] = []
    fail: ClassVar[bool] = False

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.messages: list[EmailMessage] = []
        StubSMTP.instances.append(self)

    def __enter__(self) -> "StubSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def starttls(self, context: Any) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")

    def send_message(self, message: EmailMessage) -> None:
        if StubSMTP.fail:
            raise smtplib.SMTPServerDisconnected("caiu")
        self.messages.append(message)


@pytest.fixture(autouse=True)
def stub(monkeypatch: pytest.MonkeyPatch) -> None:
    StubSMTP.instances, StubSMTP.fail = [], False
    monkeypatch.setattr(smtplib, "SMTP", StubSMTP)


def mailer() -> SmtpMailer:
    return SmtpMailer(host="smtp.x", port=587, username="u", password="p", sender="App <a@x.com>")


async def test_sends_with_starttls_and_login() -> None:
    await mailer().send("op@x.com", "Assunto", "Corpo")
    (smtp,) = StubSMTP.instances
    assert smtp.calls == ["starttls", "login:u"]
    message = smtp.messages[0]
    assert (message["To"], message["Subject"], message["From"]) == (
        "op@x.com",
        "Assunto",
        "App <a@x.com>",
    )
    assert message.get_content().strip() == "Corpo"


async def test_failure_becomes_mail_error() -> None:
    StubSMTP.fail = True
    with pytest.raises(MailError):
        await mailer().send("op@x.com", "A", "B")


async def test_fake() -> None:
    fake = FakeMailer()
    await fake.send("a@x.com", "S", "B")
    assert fake.sent[0].subject == "S"
