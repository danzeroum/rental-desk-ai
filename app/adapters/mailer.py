"""E-mail (SPEC, seção 3): convite de usuário, recuperação de senha, relatório e alertas.

SMTP com STARTTLS da biblioteca padrão, executado fora do laço de eventos. O
provedor de e-mail é pendência humana; sem configuração, usa o fake (só registra).
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from app.core.logging import get_logger

log = get_logger(__name__)


class MailError(Exception):
    pass


class Mailer(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class SmtpMailer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        timeout: float = 15,
    ) -> None:
        self.host, self.port, self.username, self.password = host, port, username, password
        self.sender, self.timeout = sender, timeout

    async def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.sender, to, subject
        message.set_content(body)
        try:
            await asyncio.to_thread(self._deliver, message)
        except (smtplib.SMTPException, OSError) as exc:
            log.warning("email_falhou", error_type=type(exc).__name__)
            raise MailError("falha ao enviar e-mail") from exc

    def _deliver(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            smtp.starttls(context=context)
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(message)


@dataclass
class SentMail:
    to: str
    subject: str
    body: str


@dataclass
class FakeMailer:
    sent: list[SentMail] = field(default_factory=list)

    async def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append(SentMail(to, subject, body))
        log.info("email_fake_registrado", subject=subject)
