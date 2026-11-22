#!/usr/bin/env bash
# Gera o arquivo .env com segredos aleatórios.
#
# - Arquivo inexistente: cria com todas as variáveis.
# - Arquivo existente (atualização de versão): só ACRESCENTA as variáveis que
#   faltam, com valores novos; nunca altera nem remove o que já está lá.
#
# Uso: deploy/gen-env.sh [arquivo] (padrão: .env na raiz do repositório)
# Variáveis opcionais: ENV (development|staging|production), DOMAIN.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:-${root}/.env}"

secret() { head -c "${1:-32}" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
aes_key() { printf 'k1:%s' "$(head -c 32 /dev/urandom | base64 | tr -d '\n')"; }

# Ordem e valores padrão de cada variável (NOME=valor).
defaults() {
  cat <<ENV
ENV=${ENV:-development}
DOMAIN=${DOMAIN:-localhost}
LOG_LEVEL=INFO
APP_VERSION=${APP_VERSION:-dev}
SECRET_KEY=$(secret 48)
DATA_ENCRYPTION_KEYS=$(aes_key)
POSTGRES_PASSWORD=$(secret)
DB_OWNER_PASSWORD=$(secret)
DB_APP_PASSWORD=$(secret)
DB_OPS_PASSWORD=$(secret)
REDIS_PASSWORD=$(secret)
WHATSAPP_PROVIDER=fake
WHATSAPP_API_BASE_URL=https://graph.facebook.com/v21.0
WHATSAPP_AUTH_HEADER=Authorization
WHATSAPP_AUTH_SCHEME=Bearer
WHATSAPP_APP_SECRET=
WHATSAPP_VERIFY_TOKEN=$(secret 24)
TRANSCRIBER_PROVIDER=fake
TRANSCRIBER_API_URL=https://api.openai.com/v1/audio/transcriptions
TRANSCRIBER_API_KEY=
TRANSCRIBER_MODEL=whisper-1
WHATSAPP_ECHO_MUTE=false
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_SENDER=rental-desk-ai <nao-responda@localhost>
LLM_PROVIDER=fake
LLM_MODEL=claude-haiku-5-5
LLM_API_KEY=
LLM_EFFORT=low
BACKUP_PASSPHRASE=$(secret 48)
BACKUP_HOUR=03
BACKUP_RETENTION_DAYS=30
ENV
}

umask 077
if [[ ! -e "${target}" ]]; then
  {
    echo "# Gerado por deploy/gen-env.sh em $(date -u +%Y-%m-%dT%H:%M:%SZ). Não versionar."
    echo "# Referência de todas as variáveis: .env.example"
    defaults
  } > "${target}"
  echo "Criado ${target} com segredos novos." >&2
  exit 0
fi

added=0
while IFS= read -r line; do
  name="${line%%=*}"
  if ! grep -q "^${name}=" "${target}"; then
    if [[ "${added}" -eq 0 ]]; then
      printf '\n# Acrescentado por deploy/gen-env.sh em %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${target}"
    fi
    echo "${line}" >> "${target}"
    echo "Acrescentada ${name} em ${target}." >&2
    added=$((added + 1))
  fi
done < <(defaults)
if [[ "${added}" -eq 0 ]]; then
  echo "${target} já está completo; mantido sem alterações." >&2
fi
