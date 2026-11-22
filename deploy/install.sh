#!/usr/bin/env bash
# Instala o rental-desk-ai numa VPS Ubuntu LTS limpa.
#
#   sudo ./deploy/install.sh                       # produção (pergunta o domínio)
#   sudo ENV=staging ./deploy/install.sh           # homologação (VPS separada)
#   sudo DOMAIN=app.exemplo.com.br ./deploy/install.sh   # sem perguntas
#
# Passos: instala o Docker (repositório oficial), gera segredos no .env, sobe os
# serviços, aplica as migrações e confere o /health. Pode ser rodado de novo:
# não sobrescreve o .env nem apaga dados.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ENV="${ENV:-production}"
SKIP_DOCKER_INSTALL="${SKIP_DOCKER_INSTALL:-0}"
SKIP_START="${SKIP_START:-0}"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERRO:\033[0m %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "rode como root (sudo ./deploy/install.sh)"
}

check_os() {
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    die "sistema suportado: Ubuntu LTS (encontrado: ${PRETTY_NAME:-desconhecido})"
  fi
  case "${VERSION_ID:-}" in
    22.04 | 24.04 | 26.04) ;;
    *) log "aviso: Ubuntu ${VERSION_ID:-?} não testado; seguindo." ;;
  esac
}

check_env() {
  case "${ENV}" in
    production | staging) ;;
    *) die "ENV deve ser production ou staging (recebido: ${ENV})" ;;
  esac
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker já instalado: $(docker --version)"
    return
  fi
  [[ "${SKIP_DOCKER_INSTALL}" == "1" ]] && die "Docker ausente e SKIP_DOCKER_INSTALL=1"
  log "Instalando Docker pelo repositório oficial"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091
  codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu ${codename} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin
  systemctl enable --now docker 2>/dev/null || true
}

ask_domain() {
  if [[ -z "${DOMAIN:-}" ]]; then
    [[ -t 0 ]] || die "defina DOMAIN (execução não interativa)"
    read -r -p "Domínio do painel (ex.: app.suaempresa.com.br): " DOMAIN
  fi
  [[ "${DOMAIN}" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "domínio inválido: ${DOMAIN}"
  export DOMAIN
}

configure_firewall() {
  if command -v ufw >/dev/null 2>&1; then
    log "Liberando SSH, HTTP e HTTPS no ufw"
    ufw allow OpenSSH >/dev/null
    ufw allow 80/tcp >/dev/null
    ufw allow 443 >/dev/null
    ufw --force enable >/dev/null
  fi
}

compose() {
  docker compose --project-directory "${ROOT}" -f "${ROOT}/deploy/docker-compose.yml" \
    --env-file "${ROOT}/.env" "$@"
}

start_services() {
  log "Construindo imagens e subindo serviços"
  compose build
  compose up -d --wait postgres redis
  log "Aplicando migrações"
  compose run --rm migrate
  compose up -d --wait
}

check_health() {
  log "Conferindo /health"
  for _ in $(seq 1 30); do
    if compose exec -T app python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" \
      >/dev/null 2>&1; then
      log "Sistema no ar: https://${DOMAIN}"
      return
    fi
    sleep 2
  done
  die "o /health não respondeu; veja 'make logs'"
}

main() {
  require_root
  check_os
  check_env
  ask_domain
  install_docker
  cd "${ROOT}"
  "${ROOT}/deploy/gen-env.sh" "${ROOT}/.env"
  if [[ "${SKIP_START}" == "1" ]]; then
    log "SKIP_START=1: instalação preparada, serviços não iniciados"
    return
  fi
  configure_firewall
  start_services
  check_health
  create_operator
}

create_operator() {
  local email="${OPERATOR_EMAIL:-}"
  if [[ -z "${email}" && -t 0 ]]; then
    read -r -p "E-mail do operador (primeiro acesso ao painel): " email
  fi
  if [[ -z "${email}" ]]; then
    log "Crie o operador depois: make operator EMAIL=voce@exemplo.com"
    return
  fi
  log "Criando o operador ${email}"
  compose exec -T app python -m app.cli create-operator --email "${email}"
}

main "$@"
