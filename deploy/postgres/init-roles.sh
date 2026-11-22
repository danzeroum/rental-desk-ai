#!/usr/bin/env bash
# Cria os papéis de banco do rental-desk-ai. Idempotente.
#
# Roda automaticamente na primeira subida do contêiner postgres
# (docker-entrypoint-initdb.d) e pode ser chamado à mão (CI, testes) com as
# variáveis PG* apontando para o servidor e as variáveis abaixo definidas.
#
#   rentaldesk_owner  dono do esquema; usado só pelas migrações
#   rentaldesk_app    caminho de requisição; NOBYPASSRLS (SPEC, seção 6)
#   rentaldesk_ops    tarefas globais do operador; BYPASSRLS explícito
set -euo pipefail

: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"
: "${DB_OWNER_PASSWORD:?}" "${DB_APP_PASSWORD:?}" "${DB_OPS_PASSWORD:?}"

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
  -v owner_pw="${DB_OWNER_PASSWORD}" \
  -v app_pw="${DB_APP_PASSWORD}" \
  -v ops_pw="${DB_OPS_PASSWORD}" \
  -v db="${POSTGRES_DB}" <<'SQL'
SELECT format('CREATE ROLE rentaldesk_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L', :'owner_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rentaldesk_owner') \gexec
SELECT format('CREATE ROLE rentaldesk_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L', :'app_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rentaldesk_app') \gexec
SELECT format('CREATE ROLE rentaldesk_ops LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS PASSWORD %L', :'ops_pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rentaldesk_ops') \gexec

-- Senhas e atributos sempre alinhados ao .env, mesmo se os papéis já existiam.
ALTER ROLE rentaldesk_owner NOSUPERUSER NOBYPASSRLS PASSWORD :'owner_pw';
ALTER ROLE rentaldesk_app NOSUPERUSER NOBYPASSRLS PASSWORD :'app_pw';
ALTER ROLE rentaldesk_ops NOSUPERUSER BYPASSRLS PASSWORD :'ops_pw';

REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO rentaldesk_owner, rentaldesk_app, rentaldesk_ops;

ALTER SCHEMA public OWNER TO rentaldesk_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO rentaldesk_app, rentaldesk_ops;

-- Tabelas criadas pelas migrações ficam acessíveis aos papéis de execução.
-- Restrições adicionais (ex.: audit_log só INSERT) vêm nas próprias migrações.
ALTER DEFAULT PRIVILEGES FOR ROLE rentaldesk_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO rentaldesk_app, rentaldesk_ops;
ALTER DEFAULT PRIVILEGES FOR ROLE rentaldesk_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO rentaldesk_app, rentaldesk_ops;
SQL
