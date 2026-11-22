"""leitura da lista de imobiliárias pelo operador (RF23) e rota por slug

O painel do operador roda no caminho de requisição, com o papel da aplicação (sem
BYPASSRLS). Esta política deixa só um usuário operador ativo, identificado em
`app.current_user_id`, LER a tabela `tenant`; as métricas de cada imobiliária
continuam lidas com `app.current_tenant_id` e as políticas normais. Criar
imobiliária segue com o papel de operação (comando no contêiner do scheduler).

`tenant_route` passa a aceitar o canal `slug`: o link público do aviso de
privacidade acha a imobiliária pela leitura global das rotas, sem afrouxar o RLS.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-22 03:00:00+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
CREATE FUNCTION app_current_user_is_operator() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT EXISTS (
    SELECT 1 FROM app_user
     WHERE id = app_current_user() AND is_operator AND is_active
  )
$$;
CREATE POLICY tenant_operator_read ON tenant FOR SELECT
  USING (app_current_user_is_operator());
ALTER TABLE tenant_route DROP CONSTRAINT ck_tenant_route_channel;
ALTER TABLE tenant_route ADD CONSTRAINT ck_tenant_route_channel
  CHECK (channel IN ('whatsapp', 'slug'));
"""
    )


def downgrade() -> None:
    op.execute(
        """
ALTER TABLE tenant_route NO FORCE ROW LEVEL SECURITY;  -- o dono apaga sem filtro de tenant
DELETE FROM tenant_route WHERE channel = 'slug';
ALTER TABLE tenant_route FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_route DROP CONSTRAINT ck_tenant_route_channel;
ALTER TABLE tenant_route ADD CONSTRAINT ck_tenant_route_channel CHECK (channel IN ('whatsapp'));
DROP POLICY IF EXISTS tenant_operator_read ON tenant;
DROP FUNCTION IF EXISTS app_current_user_is_operator();
"""
    )
