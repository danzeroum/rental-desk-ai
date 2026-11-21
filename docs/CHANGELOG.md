# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versionamento semântico (veja `DECISIONS.md`, D007).

## [Não lançado]

## [0.4.0] - 2026-11-21 · M3 Cálculo de valor e busca

### Adicionado
- Cálculo do valor estimado com tarifas por noite, pacotes (fechado/aberto), mínimo de noites e
  taxa de limpeza, em centavos.
- Busca com filtros obrigatórios, orçamento, dados vencidos e ranking estável (até 3 casas).
- Exigência de cobertura de 90% nos módulos centrais na CI.

## [0.3.0] - 2026-11-21 · M2 Planilha modelo, importação e aprovação

### Adicionado
- Planilha modelo com validação de células (`make template`) e exemplo Demo Riviera com 10 casas.
- Importador com erros por aba/linha/coluna e avisos não bloqueantes (tarifa futura, lacuna de
  tarifa, capacidade, link de fotos fora do ar).
- Checagem HTTP dos links de fotos com proteção contra SSRF.
- Versões dos dados (snapshot) com hash, aprovação registrada e rejeição.
- Comando `python -m app.importer` (template, sample, check).

## [0.2.0] - 2026-11-21 · M1 Modelo de dados, RLS e isolamento

### Adicionado
- Modelos e migração inicial das tabelas da seção 6 do SPEC.
- Row-Level Security forçado em todas as tabelas de negócio, com falha fechada.
- Sessões com escopo de tenant e filtro automático por `tenant_id` nas consultas ORM.
- Trilha de auditoria só de inclusão, encadeada por hash por tenant, com verificação de integridade.
- Testes de isolamento entre imobiliárias que cobrem todas as tabelas automaticamente.

## [0.1.0] - 2026-11-21 · M0 Esqueleto

### Adicionado
- Esqueleto do monólito modular (FastAPI), processos `worker` e `scheduler` com heartbeat.
- `/health/live` e `/health` (Postgres e Redis).
- Logs JSON com `tenant_id`, `conversation_id`, `trace_id` e mascaramento de dados pessoais.
- Docker Compose com Caddy (HTTPS), Postgres 16, Redis 7 e backup diário cifrado.
- Papéis de banco com menor privilégio (`rentaldesk_owner`, `rentaldesk_app`, `rentaldesk_ops`).
- `install.sh` para VPS Ubuntu LTS, `gen-env.sh`, `Makefile`.
- CI: lint, tipos, testes, auditoria de dependências, SAST, segredos, imagem e teste de fumaça.
- Roteiro da verificação técnica de coexistência e capturador de webhook.
