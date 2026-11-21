#!/usr/bin/env bash
# Gera o arquivo .env com segredos aleatórios. Nunca sobrescreve um .env existente.
#
# Uso: deploy/gen-env.sh [arquivo] (padrão: .env na raiz do repositório)
# Variáveis opcionais: ENV (development|staging|production), DOMAIN.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:-${root}/.env}"

if [[ -e "${target}" ]]; then
  echo "${target} já existe; mantido sem alterações." >&2
  exit 0
fi

secret() { head -c "${1:-32}" /dev/urandom | od -An -tx1 | tr -d ' \n'; }

umask 077
cat > "${target}" <<ENV
# Gerado por deploy/gen-env.sh em $(date -u +%Y-%m-%dT%H:%M:%SZ). Não versionar.
# Referência de todas as variáveis: .env.example
ENV=${ENV:-development}
DOMAIN=${DOMAIN:-localhost}
LOG_LEVEL=INFO
APP_VERSION=${APP_VERSION:-dev}

SECRET_KEY=$(secret 48)

POSTGRES_PASSWORD=$(secret)
DB_OWNER_PASSWORD=$(secret)
DB_APP_PASSWORD=$(secret)
DB_OPS_PASSWORD=$(secret)
REDIS_PASSWORD=$(secret)

BACKUP_PASSPHRASE=$(secret 48)
BACKUP_HOUR=03
BACKUP_RETENTION_DAYS=30
ENV
echo "Criado ${target} com segredos novos." >&2
