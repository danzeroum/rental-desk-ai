"""links do kit de demonstracao

`demo_link` é global (sem `tenant_id`, como `app_user`): o link abre só as imobiliárias
marcadas `is_demo`. Guarda o hash do token. Sem RLS de tenant; o papel do app lê e grava.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-22 18:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "demo_link",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("visits", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name=op.f("ck_demo_link_revocation_order")),
        sa.ForeignKeyConstraint(["created_by"], ["app_user.id"], name=op.f("fk_demo_link_created_by_app_user"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_link")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_demo_link_token_hash")),
    )  # fmt: skip
    op.execute("REVOKE DELETE, TRUNCATE ON demo_link FROM rentaldesk_app")


def downgrade() -> None:
    op.drop_table("demo_link")
