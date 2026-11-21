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
.PHONY: up down logs ps migrate seed-demo template backup restore
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

seed-demo: ## Tenant de demonstração (RF25, M7)
	@echo "make seed-demo: disponível a partir do M7" >&2; exit 2

template: ## Gera a planilha modelo (planilha-modelo.xlsx) e o exemplo Demo Riviera
	$(UV) python -m app.importer template --out planilha-modelo.xlsx
	$(UV) python -m app.importer sample --out exemplo-demo-riviera.xlsx

restore: ## Restaura um backup (M8)
	@echo "make restore: disponível a partir do M8" >&2; exit 2

# --- Desenvolvimento ----------------------------------------------------------
.PHONY: dev fmt lint test test-unit test-deps-up test-deps-down golden golden-live shellcheck security
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
	$(UV) pytest tests/unit tests/integration --cov --cov-report=term-missing

test-unit: ## Só testes de unidade (sem Postgres/Redis)
	$(UV) pytest tests/unit

test-deps-up: ## Postgres e Redis de teste (portas 55432 e 56379)
	$(TEST_COMPOSE) up -d --wait

test-deps-down:
	$(TEST_COMPOSE) down -v

golden: ## Conjunto de ouro com FakeLLM (M5)
	@echo "make golden: disponível a partir do M5" >&2; exit 2

golden-live: ## Conjunto de ouro com o LLM real (M5)
	@echo "make golden-live: disponível a partir do M5" >&2; exit 2

security: ## Auditoria de dependências e SAST local
	uv export --frozen --no-dev --no-hashes --format requirements-txt -o /tmp/rental-desk-requirements.txt
	$(UV) pip-audit --strict -r /tmp/rental-desk-requirements.txt
	$(UV) bandit -q -c pyproject.toml -r app
