"""Links de acesso ao kit de demonstração para vendas (sem login de painel)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAt, UuidPk


class DemoLink(Base):
    """Link com token para o simulador de vendas. Global (não pertence a imobiliária): dá
    acesso só às imobiliárias `is_demo`. Guarda o SHA-256 do token, nunca o token."""

    __tablename__ = "demo_link"
    __table_args__ = (
        CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="revocation_order"),
    )

    id: Mapped[UuidPk]
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(120))
    """Para quem o link foi enviado (ex.: "visita à Imobiliária X"). Sem telefone nem e-mail."""
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[CreatedAt]
    revoked_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]
    visits: Mapped[int] = mapped_column(Integer, server_default=text("0"))
