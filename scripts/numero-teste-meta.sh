#!/usr/bin/env bash
# Liga o número de teste da Meta (Cloud API) à "Imobiliária Demo Riviera".
# Guia: docs/numero-teste-meta.md
#
# Rode na raiz do repositório, no servidor já instalado:
#   bash scripts/numero-teste-meta.sh <phone_number_id>
#
# Pede, sem mostrar na tela, a chave secreta do app e o token de acesso. A chave vai para
# o .env (o sistema confere com ela a assinatura de cada mensagem da Meta); o token é
# cifrado no banco. Nada disso aparece no terminal nem no histórico do shell.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

PNID="${1:-}"
[[ "${PNID}" =~ ^[0-9]{6,20}$ ]] || { echo "uso: bash scripts/numero-teste-meta.sh <phone_number_id (só números)>" >&2; exit 2; }
[[ -f .env ]] || { echo "ERRO: .env não encontrado; instale o sistema antes (deploy/install.sh)." >&2; exit 1; }

info() { printf '    %s\n' "$*"; }
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

SUDO=()
docker ps > /dev/null 2>&1 || SUDO=(sudo)
compose() { "${SUDO[@]}" docker compose --project-directory . -f deploy/docker-compose.yml --env-file .env "$@"; }

set_env() {  # troca ou acrescenta NOME=valor sem mostrar o valor
  local tmp; tmp="$(mktemp)"
  grep -vE "^$1=" .env > "${tmp}" || true
  printf '%s=%s\n' "$1" "$2" >> "${tmp}"
  cat "${tmp}" > .env && rm -f "${tmp}"
}
env_value() {  # valor de NOME no .env, ou vazio se não existir (sem derrubar o set -e)
  { grep -E "^$1=" .env || true; } | tail -n 1 | cut -d= -f2-
}

step "1/4 Credenciais da Meta (não aparecem na tela)"
read -rsp "    Chave secreta do app (Configurações do app → Básico): " APP_SECRET; echo
read -rsp "    Token de acesso (usuário do sistema ou o de teste, 24 h): " DEMO_WHATSAPP_TOKEN; echo
[[ -n "${APP_SECRET}" && -n "${DEMO_WHATSAPP_TOKEN}" ]] || { echo "ERRO: chave e token são obrigatórios." >&2; exit 1; }
export DEMO_WHATSAPP_TOKEN

step "2/4 Ligando o provedor oficial (Cloud API) no .env"
set_env WHATSAPP_PROVIDER cloud_api
set_env WHATSAPP_APP_SECRET "${APP_SECRET}"
unset APP_SECRET
[[ -n "$(env_value WHATSAPP_VERIFY_TOKEN)" ]] || set_env WHATSAPP_VERIFY_TOKEN "$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
chmod 600 .env
compose up -d --wait > /dev/null
info "serviços reiniciados com a configuração nova"

step "3/4 Conectando o número ${PNID} à Imobiliária Demo Riviera"
if [[ ${#SUDO[@]} -gt 0 ]]; then
  sudo --preserve-env=DEMO_WHATSAPP_TOKEN docker compose --project-directory . -f deploy/docker-compose.yml \
    --env-file .env exec -T -e DEMO_WHATSAPP_TOKEN scheduler python -m app.cli seed-demo --phone-number-id "${PNID}"
else
  compose exec -T -e DEMO_WHATSAPP_TOKEN scheduler python -m app.cli seed-demo --phone-number-id "${PNID}"
fi
unset DEMO_WHATSAPP_TOKEN

step "4/4 Conferindo o webhook como a Meta vai conferir"
DOMAIN="$(env_value DOMAIN)"
BASE="$(env_value PUBLIC_BASE_URL)"; BASE="${BASE:-https://${DOMAIN}}"
VERIFY="$(env_value WHATSAPP_VERIFY_TOKEN)"
ANSWER="$(curl -s --max-time 15 "${BASE}/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=${VERIFY}&hub.challenge=rental-desk-ok" || true)"
if [[ "${ANSWER}" == "rental-desk-ok" ]]; then
  info "webhook respondeu certo em ${BASE}/webhooks/whatsapp"
else
  info "ATENÇÃO: o webhook não respondeu como esperado (${ANSWER:-sem resposta}). Veja: docs/numero-teste-meta.md"
fi

printf '\n\033[1;32mPronto. Agora, no painel da Meta (WhatsApp → Configuração → Webhook → Editar):\033[0m\n'
info "URL de callback:       ${BASE}/webhooks/whatsapp"
info "Token de verificação:  ${VERIFY}"
info "Clique em 'Verificar e salvar' e assine o campo 'messages'."
info ""
info "Depois, mande do seu WhatsApp para o número de teste: Casa para 8 pessoas de 28/12 a 02/01"
info "e veja a conversa no painel (Operador → Entrar como suporte → Demo Riviera)."
info "Se a Meta ainda bloquear os envios (erro 130497), ponha a Demo Riviera em modo sombra"
info "(Situação → voltar ao modo sombra): as respostas aparecem em 'Situação' sem enviar."
