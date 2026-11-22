#!/usr/bin/env bash
# Sobe o Rental Desk AI no seu computador para teste (guia: docs/teste-local.md).
#
# O que faz, em ordem (pode rodar de novo: cada passo é idempotente):
#   1. Confere git, Docker (com Compose v2), make e curl.
#   2. Escolhe as portas: 80/443 se livres; senão 8080/8443.
#   3. Gera o .env com segredos aleatórios (deploy/gen-env.sh) e ajusta portas e endereço.
#   4. Constrói a imagem, aplica as migrações e sobe tudo (Caddy, app, worker, scheduler,
#      Postgres, Redis, backup).
#   5. Cria o operador e imprime o convite; cria as imobiliárias de demonstração.
#   6. Cria um link do kit de demonstração para vendas.
#   7. Com --testes, roda lint e a suíte de testes (precisa de uv).
#   8. Grava ACESSO-LOCAL.md (fora do git) com os endereços e os próximos passos.
#
# Uso (na raiz do repositório):
#   bash scripts/teste-local.sh [--testes] [--email voce@exemplo.com]
# Variáveis opcionais no shell: LLM_API_KEY (IA real no lugar do FakeLLM), LLM_MODEL.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

RUN_TESTS=0
OPERATOR_EMAIL="operador@rentaldesk.local"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --testes) RUN_TESTS=1 ;;
    --email) OPERATOR_EMAIL="${2:?informe o e-mail depois de --email}"; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "opção desconhecida: $1 (use --help)" >&2; exit 2 ;;
  esac
  shift
done

LOG="${ROOT}/teste-local.log"
: > "${LOG}"
step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; echo "==> $*" >> "${LOG}"; }
info() { printf '    %s\n' "$*"; }
fail() { printf '\n\033[1;31mERRO:\033[0m %s\n' "$*" >&2; exit 1; }
run() { "$@" >> "${LOG}" 2>&1 || fail "falhou: $* (detalhes em teste-local.log)"; }

# --- 1. Pré-requisitos -------------------------------------------------------------
step "1/8 Conferindo os programas necessários"
for cmd in git docker make curl; do
  command -v "${cmd}" > /dev/null || fail "instale '${cmd}' e rode de novo"
  info "${cmd}: ok"
done
docker info > /dev/null 2>&1 || fail "o Docker não está rodando (abra o Docker Desktop ou: sudo systemctl start docker)"
docker compose version > /dev/null 2>&1 || fail "precisa do Docker Compose v2 ('docker compose')"
info "docker compose: $(docker compose version --short)"

# --- 2. Portas -----------------------------------------------------------------------
step "2/8 Escolhendo as portas"
port_busy() { (exec 3<> "/dev/tcp/127.0.0.1/$1") 2> /dev/null; }
running_here() { docker compose --project-directory . -f deploy/docker-compose.yml --env-file .env ps -q caddy 2> /dev/null | grep -q .; }
current() { [[ -f .env ]] && grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true; }
HTTP_PORT="$(current HTTP_PORT)"; HTTPS_PORT="$(current HTTPS_PORT)"
if [[ -z "${HTTPS_PORT}" ]]; then
  if [[ -f .env ]] && running_here; then
    HTTP_PORT=80; HTTPS_PORT=443
  elif port_busy 80 || port_busy 443; then
    HTTP_PORT=8080; HTTPS_PORT=8443
    port_busy 8443 && fail "as portas 443 e 8443 estão ocupadas; defina HTTPS_PORT e HTTP_PORT no .env"
  else
    HTTP_PORT=80; HTTPS_PORT=443
  fi
fi
BASE_URL="https://localhost"
[[ "${HTTPS_PORT}" != "443" ]] && BASE_URL="https://localhost:${HTTPS_PORT}"
info "painel em ${BASE_URL}"

# --- 3. .env ---------------------------------------------------------------------------
step "3/8 Preparando o .env (segredos aleatórios, nunca vão para o git)"
if [[ ! -f .env ]]; then
  ENV=development DOMAIN=localhost deploy/gen-env.sh .env 2>> "${LOG}"
  info "criado .env novo"
else
  deploy/gen-env.sh .env 2>> "${LOG}"
  info ".env já existia: mantido (só faltantes acrescentados)"
fi
set_env() {  # set_env NOME valor — troca ou acrescenta, sem mostrar o valor
  local tmp; tmp="$(mktemp)"
  grep -vE "^$1=" .env > "${tmp}" || true
  printf '%s=%s\n' "$1" "$2" >> "${tmp}"
  cat "${tmp}" > .env && rm -f "${tmp}"
}
set_env HTTP_PORT "${HTTP_PORT}"
set_env HTTPS_PORT "${HTTPS_PORT}"
set_env PUBLIC_BASE_URL "${BASE_URL}"
if [[ -n "${LLM_API_KEY:-}" ]]; then
  set_env LLM_PROVIDER anthropic
  set_env LLM_API_KEY "${LLM_API_KEY}"
  [[ -n "${LLM_MODEL:-}" ]] && set_env LLM_MODEL "${LLM_MODEL}"
  info "IA real ligada (LLM_API_KEY do shell gravada no .env)"
else
  info "IA: FakeLLM (sem chave). Para a IA real: LLM_API_KEY=... bash scripts/teste-local.sh"
fi
chmod 600 .env

# --- 4. Subir --------------------------------------------------------------------------
step "4/8 Construindo a imagem e subindo os serviços (a primeira vez leva alguns minutos)"
COMPOSE=(docker compose --project-directory . -f deploy/docker-compose.yml --env-file .env)
run "${COMPOSE[@]}" build
info "imagem construída"
run "${COMPOSE[@]}" up -d --wait postgres redis
info "Postgres e Redis no ar"
run "${COMPOSE[@]}" run --rm migrate
info "migrações aplicadas (alembic upgrade head)"
run "${COMPOSE[@]}" up -d --wait
info "todos os serviços saudáveis"
for _ in $(seq 1 30); do
  curl -skf "${BASE_URL}/health" > /dev/null 2>&1 && break
  sleep 2
done
curl -skf "${BASE_URL}/health" > /dev/null 2>&1 || fail "o /health não respondeu em ${BASE_URL} (veja: make logs)"
info "/health respondeu pelo Caddy em HTTPS"

# --- 5. Operador e demonstração ----------------------------------------------------
step "5/8 Criando o operador e as imobiliárias de demonstração"
INVITE="$("${COMPOSE[@]}" exec -T app python -m app.cli create-operator --email "${OPERATOR_EMAIL}" 2>> "${LOG}" | tail -n 1)" \
  || fail "não consegui criar o operador (detalhes em teste-local.log)"
info "operador: ${OPERATOR_EMAIL}"
SEED="$("${COMPOSE[@]}" exec -T scheduler python -m app.cli seed-demo 2>> "${LOG}")" \
  || fail "não consegui criar a demonstração (detalhes em teste-local.log)"
while IFS= read -r line; do info "${line}"; done <<< "${SEED}"

# --- 6. Link do kit de vendas -------------------------------------------------------
step "6/8 Criando um link do kit de demonstração"
DEMO_LINK="$("${COMPOSE[@]}" exec -T app python -m app.cli create-demo-link --label "teste local $(date +%d/%m)" 2>> "${LOG}" | tail -n 1)" \
  || fail "não consegui criar o link da demo (detalhes em teste-local.log)"
info "link criado (revogável em ${BASE_URL}/operador/demo)"

# --- 7. Testes (opcional) -----------------------------------------------------------
step "7/8 Testes automáticos"
TESTS="não rodados (use --testes; precisa de uv: https://docs.astral.sh/uv/)"
if [[ "${RUN_TESTS}" -eq 1 ]]; then
  command -v uv > /dev/null || fail "para --testes, instale o uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
  run make dev
  run make test-deps-up
  info "rodando lint (ruff, mypy, shellcheck)..."
  run make lint
  info "rodando a suíte (unidade, integração, sistema), leva ~8 min..."
  run make test
  info "conjunto de ouro (100 conversas, FakeLLM)..."
  run make golden
  run make test-deps-down
  TESTS="lint, suíte completa e conjunto de ouro: verdes (saída em teste-local.log)"
fi
info "${TESTS}"

# --- 8. Resumo -----------------------------------------------------------------------
step "8/8 Gravando ACESSO-LOCAL.md"
grep -qxF "ACESSO-LOCAL.md" .gitignore || echo "ACESSO-LOCAL.md" >> .gitignore
grep -qxF "teste-local.log" .gitignore || echo "teste-local.log" >> .gitignore
cat > ACESSO-LOCAL.md <<EOF
# Acesso local — gerado em $(date '+%d/%m/%Y %H:%M')

Arquivo só seu (fora do git). Guia completo: docs/teste-local.md.

| O quê | Endereço |
|---|---|
| Painel | ${BASE_URL} |
| Convite do operador (vale 72 h, uso único) | ${INVITE} |
| Kit de demonstração (abra no celular ou no navegador) | ${DEMO_LINK} |
| Saúde do sistema | ${BASE_URL}/status |
| Ajuda | ${BASE_URL}/ajuda |

O navegador vai avisar que o certificado não é confiável: é o certificado local do Caddy. Aceite
("Avançado" → "Continuar"). No celular na mesma rede, troque "localhost" pelo IP do computador.

1. Abra o convite, crie a senha e cadastre o autenticador (Google Authenticator, Authy, 1Password).
2. Você cai na área do operador: saúde das imobiliárias e "Demonstração" (links do kit).
3. "Minhas imobiliárias" → "Entrar como suporte" na Demo Riviera para ver o painel do gestor.

Testes: ${TESTS}
IA: $( [[ -n "${LLM_API_KEY:-}" ]] && echo "real (anthropic)" || echo "FakeLLM (sem chave)" )

Comandos: make ps · make logs · make down (para, mantém os dados) · make up (sobe de novo)
EOF
chmod 600 ACESSO-LOCAL.md

printf '\n\033[1;32mPronto!\033[0m\n'
info "Painel:          ${BASE_URL}"
info "Convite:         ${INVITE}"
info "Demonstração:    ${DEMO_LINK}"
info "Tudo anotado em ACESSO-LOCAL.md; o roteiro de teste está em docs/teste-local.md."
