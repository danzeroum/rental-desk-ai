"""Motivo do acesso do suporte a dados de uma imobiliária (revisão LGPD, Risco-004). Puro.

O operador (fornecedor) só entra numa imobiliária ou consulta o rastreio dizendo por quê: a
trilha guarda quem, quando **e para quê** (LGPD, Art. 6º, I e X; Art. 37). O detalhe é texto
livre curto (ex.: número do chamado) e passa pela máscara de dados pessoais.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.core.masking import mask_pii

REASONS: Final = {
    "chamado": "Chamado do cliente",
    "comprovacao": "Comprovação pedida pelo cliente",
    "implantacao": "Implantação ou configuração",
    "incidente": "Incidente ou falha",
    "auditoria": "Auditoria interna",
}
MAX_DETAIL: Final = 120


@dataclass(frozen=True)
class AccessReason:
    code: str
    detail: str = ""

    @property
    def label(self) -> str:
        return REASONS[self.code]

    def as_audit(self) -> dict[str, str]:
        return {"reason": self.code, "reason_detail": self.detail}

    def describe(self) -> str:
        return f"{self.label}: {self.detail}" if self.detail else self.label


def parse_reason(code: str | None, detail: str | None = "") -> AccessReason | None:
    """`None` quando o motivo não foi escolhido (ou não é um dos previstos)."""
    if not code or code not in REASONS:
        return None
    cleaned = mask_pii(" ".join((detail or "").split()))[:MAX_DETAIL]
    return AccessReason(code, cleaned)


def describe_payload(payload: dict[str, object]) -> str:
    """Motivo gravado num evento da trilha, legível ("" se não houver)."""
    code = str(payload.get("reason") or "")
    if code not in REASONS:
        return ""
    return AccessReason(code, str(payload.get("reason_detail") or "")).describe()
