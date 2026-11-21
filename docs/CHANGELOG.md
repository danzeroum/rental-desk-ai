# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versionamento semântico (veja `DECISIONS.md`, D007).

## [Não lançado]

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
