"""status de entrega das mensagens de saida

A Meta informa, por webhook, se cada mensagem nossa foi enviada, entregue, lida ou falhou
(com código). Guardamos o último status na própria mensagem; a trilha imutável recebe um
evento `delivery_status` por mudança.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-24 22:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("message", sa.Column("delivery_status", sa.String(length=12), nullable=True))
    op.add_column("message", sa.Column("delivery_status_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("message", sa.Column("delivery_error_code", sa.Integer(), nullable=True))
    op.add_column("message", sa.Column("delivery_error", sa.String(length=200), nullable=True))
    op.create_check_constraint(
        op.f("ck_message_delivery_status"), "message",
        "delivery_status IS NULL OR delivery_status IN ('sent', 'delivered', 'read', 'failed')",
    )
    op.create_index(
        "ix_message_delivery_failed", "message", ["tenant_id", "delivery_status_at"],
        postgresql_where=sa.text("delivery_status = 'failed'"),
    )


def downgrade() -> None:
    op.drop_index("ix_message_delivery_failed", table_name="message")
    op.drop_constraint(op.f("ck_message_delivery_status"), "message", type_="check")
    for column in ("delivery_error", "delivery_error_code", "delivery_status_at", "delivery_status"):
        op.drop_column("message", column)
