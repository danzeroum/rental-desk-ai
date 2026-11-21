# CLAUDE.md — rental-desk-ai

Assistente de reservas com IA no WhatsApp para imobiliárias de aluguel por temporada.
**Leia `docs/SPEC.md` antes de qualquer tarefa.** Ele é a fonte da verdade.

## Regras inegociáveis

1. **A IA nunca decide fatos.** Casa, preço, data, disponibilidade e capacidade vêm só do banco e do código. O LLM extrai intenção (JSON com esquema) e escreve frases curtas **sem números, datas, valores ou códigos**.
2. **Cards de imóveis só por template Jinja.** Valores, datas, códigos e o aviso "sujeito à confirmação do corretor" nunca passam pelo LLM.
3. **Multi-tenant seguro:** toda tabela de negócio tem `tenant_id`, RLS ativo no Postgres e filtro na aplicação. Nunca use um papel de banco com `BYPASSRLS` no caminho de requisição.
4. **Dinheiro em centavos (int).** Noites: check-in inclusivo, check-out exclusivo. Fuso `America/Sao_Paulo`.
5. **Sem dados pessoais no LLM nem nos logs:** não envie nome/telefone; mascare CPF, cartão, e-mail e chave Pix.
6. **Só API oficial do WhatsApp** (Cloud API, via parceiro ou Meta). Proibido WhatsApp Web, QR code, Baileys, Evolution, WAHA e similares.
7. **Nenhum segredo no código.** Configuração por variáveis de ambiente (12-Factor). `.env.example` sempre atualizado.
8. Toda resposta enviada grava `audit_log` (append-only, encadeado por hash).

## Stack

Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, Pydantic v2, PostgreSQL 16, Redis 7, Jinja2 + HTMX, Docker Compose, Caddy, pytest, ruff, mypy.
Adaptadores com interface + implementação fake: `WhatsAppGateway`, `LLMClient`, `Transcriber`, `Mailer`. Provedor e modelo por env; nada hardcoded.

## Estrutura

```
app/
  api/          # webhook, rotas do painel
  core/         # config, db, rls, logging, security
  domain/       # pricing, search, state, decision, validator, templates (puro, sem I/O)
  adapters/     # whatsapp, llm, transcriber, mailer (+ fakes)
  importer/     # planilha modelo: gerar, ler, validar, snapshot
  workers/      # fila, debounce, pipeline, escalonamento
  scheduler/    # lembrete 9h, relatório, retenção, backups
  web/          # templates Jinja do painel
migrations/
tests/
  unit/ integration/ system/ golden/ load/
deploy/         # docker-compose.yml, Caddyfile, install.sh
docs/           # SPEC.md, PROGRESS.md, DECISIONS.md, runbook.md, verificacao-coexistencia.md, CHANGELOG.md
```

## Comandos

- `make up` / `make down` / `make logs`
- `make migrate` · `make seed-demo` (tenant de demonstração)
- `make test` (unit + integration) · `make golden` (conjunto de ouro com FakeLLM) · `make golden-live` (LLM real)
- `make lint` (ruff + mypy) · `make template` (gera a planilha modelo)
- `make backup` / `make restore`

## Forma de trabalhar

- Trabalhe **um marco por vez** (seção 12 do SPEC). Para cada marco: plano curto → testes primeiro nos módulos de domínio → implementação → `make lint test` verde → atualizar `docs/PROGRESS.md` → commit.
- Um branch e um PR por marco, com descrição do que foi feito, como testar e o que ficou de fora.
- Decisões que não estão no SPEC: registre em `docs/DECISIONS.md` (contexto, opção escolhida, alternativas).
- Se o SPEC estiver ambíguo ou faltar informação para um passo, **pare e pergunte** escrevendo `INCOMPLETE_PLAN: <dúvida>`. Não invente regra de negócio.
- Itens que dependem de humano (contas, chaves, verificação de coexistência, jurídico): crie a interface + fake, documente o passo em `docs/` e siga.
- Isto é a **Versão 1.0 de um produto vendido**, não um protótipo: qualidade de produção em todo marco (testes, logs, tratamento de erro, mensagens de erro claras no painel). Não deixe "TODO para depois" em caminho que o cliente usa.
- Mantenha funções curtas, nomes descritivos, sem duplicação. O pacote `domain/` não importa nada de I/O.
- Testes cobrem limites: período que cruza tarifas, pacote parcial, mínimo de noites, check-in = check-out, capacidade exata, dados com 24 h e 1 min.

## Não faça

- Não adicione dependências pesadas sem registrar em `DECISIONS.md`.
- Não crie microsserviços, Kubernetes ou frontend SPA.
- Não envie mensagens ativas de WhatsApp a quem não tem consentimento registrado.
- Não desative RLS, validação de assinatura do webhook ou o validador para "fazer o teste passar".
