# rental-desk-ai — comandos do dia a dia. `make help` lista tudo.
SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

COMPOSE      := docker compose --project-directory . -f deploy/docker-compose.yml --env-file .env
TEST_COMPOSE := docker compose --project-directory . -f deploy/docker-compose.test.yml
UV           := uv run
SCRIPTS      := deploy/*.sh deploy/backup/*.sh deploy/postgres/*.sh

.PHONY: help
help: ## Lista os comandos
	@grep -hE '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.env:
	@deploy/gen-env.sh .env

# --- Sistema -----------------------------------------------------------------
.PHONY: up down logs ps migrate operator tenant reset-totp seed-demo template backup restore restore-drill
up: .env ## Sobe tudo (gera .env com segredos na primeira vez)
	$(COMPOSE) up -d --build --wait

down: ## Para tudo (mantém os dados)
	$(COMPOSE) down

logs: ## Acompanha os logs
	$(COMPOSE) logs -f --tail=200

ps: ## Estado dos serviços
	$(COMPOSE) ps

migrate: .env ## Aplica as migrações do banco
	$(COMPOSE) run --rm --build migrate

backup: ## Gera um backup cifrado agora
	$(COMPOSE) exec backup backup-now

operator: ## Cria o operador e imprime o convite (EMAIL=voce@exemplo.com)
	@test -n "$(EMAIL)" || { echo "use: make operator EMAIL=voce@exemplo.com" >&2; exit 2; }
	$(COMPOSE) exec -T app python -m app.cli create-operator --email "$(EMAIL)"

tenant: ## Cria imobiliária e convida o gestor (NAME="Imobiliária Sol" EMAIL=gestor@...)
	@test -n "$(NAME)" -a -n "$(EMAIL)" || { echo 'use: make tenant NAME="Imobiliária Sol" EMAIL=gestor@exemplo.com' >&2; exit 2; }
	$(COMPOSE) exec -T scheduler python -m app.cli create-tenant --name "$(NAME)" --gestor-email "$(EMAIL)"

reset-totp: ## Apaga o autenticador de quem perdeu o celular (EMAIL=...)
	@test -n "$(EMAIL)" || { echo "use: make reset-totp EMAIL=pessoa@exemplo.com" >&2; exit 2; }
	$(COMPOSE) exec -T app python -m app.cli reset-totp --email "$(EMAIL)"

seed-demo: ## Cria/renova a Imobiliária Demo Riviera (RF25); PNID=<número de teste> e DEMO_WHATSAPP_TOKEN no shell
	$(COMPOSE) exec -T -e DEMO_WHATSAPP_TOKEN scheduler python -m app.cli seed-demo $(if $(PNID),--phone-number-id "$(PNID)")

template: ## Gera a planilha modelo (planilha-modelo.xlsx) e o exemplo Demo Riviera
	$(UV) python -m app.importer template --out planilha-modelo.xlsx
	$(UV) python -m app.importer sample --out exemplo-demo-riviera.xlsx

restore: ## Restaura um backup e troca o banco (FILE=/backups/... | latest | remote); para app, worker e scheduler antes
	@test "$(CONFIRM)" = "sim" || { echo 'confirme: make restore FILE=latest CONFIRM=sim (o banco atual fica guardado como rentaldesk_antes_<data>)' >&2; exit 2; }
	$(COMPOSE) stop app worker scheduler
	$(COMPOSE) exec -T backup restore-backup "$(or $(FILE),latest)" --swap
	$(COMPOSE) run --rm migrate
	$(COMPOSE) up -d --wait

restore-drill: ## Exercício de restauração (RTO): restaura o último backup num banco à parte, confere e apaga
	$(COMPOSE) exec -T backup restore-backup "$(or $(FILE),latest)"

# --- Desenvolvimento ----------------------------------------------------------
.PHONY: dev fmt lint test coverage-core test-unit test-deps-up test-deps-down golden golden-live shellcheck security
dev: ## Instala as dependências locais (uv)
	uv sync --frozen

fmt: ## Formata o código
	$(UV) ruff format .
	$(UV) ruff check --fix .

lint: ## ruff + mypy + shellcheck
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) mypy
	@$(MAKE) --no-print-directory shellcheck

shellcheck:
	uvx --from shellcheck-py==0.11.0.1 shellcheck $(SCRIPTS)

test: ## Testes de unidade e integração, com cobertura (precisa de make test-deps-up)
	$(UV) pytest tests/unit tests/integration tests/system --cov --cov-report=term-missing
	@$(MAKE) --no-print-directory coverage-core

# SPEC, seção 5: cobertura ≥ 80% nos módulos centrais (aqui exigimos 90%).
CORE_MODULES := app/domain/*,app/importer/*,app/workers/engine.py,app/core/masking.py
coverage-core:
	$(UV) coverage report --include='$(CORE_MODULES)' --fail-under=90 --skip-covered

test-unit: ## Só testes de unidade (sem Postgres/Redis)
	$(UV) pytest tests/unit

test-deps-up: ## Postgres e Redis de teste (portas 55432 e 56379)
	$(TEST_COMPOSE) up -d --wait

test-deps-down:
	$(TEST_COMPOSE) down -v

golden: ## Conjunto de ouro com o FakeLLM determinístico
	$(UV) pytest tests/golden -q -s

golden-live: ## Compara Haiku, Sonnet e Opus no conjunto de ouro (precisa de LLM_API_KEY)
	$(UV) python -m tests.golden.compare --out docs/golden/comparacao-$$(date +%F).md

security: ## Auditoria de dependências, SAST e segredos no histórico (gitleaks via Docker)
	uv export --frozen --no-dev --no-hashes --format requirements-txt -o /tmp/rental-desk-requirements.txt
	$(UV) pip-audit --strict -r /tmp/rental-desk-requirements.txt
	$(UV) bandit -q -c pyproject.toml -r app
	docker run --rm -v "$(CURDIR):/repo" ghcr.io/gitleaks/gitleaks:v8.28.0 git /repo --redact
