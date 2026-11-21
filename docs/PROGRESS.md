# Progresso

Marcos do SPEC (seção 12). Atualizado ao fim de cada marco.

| Marco | Estado | PR | Versão |
|---|---|---|---|
| M0 · Esqueleto | ✅ concluído | (link no PR do M0) | 0.1.0 |
| M1 · Modelo de dados, RLS, isolamento | ⏳ próximo | | |
| M2 · Planilha: gerador, importador, snapshot, aprovação | — | | |
| M3 · `pricing` + `search` (TDD) | — | | |
| M4 · Webhook, gateway, debounce, rate limit, transcrição, estado | — | | |
| M5 · Extração, decisão, templates, validador, auditoria | — | | |
| M6 · Passagem, assumir, escalonamento, pausa, desfecho | — | | |
| M7 · Painel, lembrete 9h, relatório, demo, direitos do titular | — | | |
| M8 · Backup/restore, monitoramento, carga, modo sombra, runbook | — | | |
| M9 · iCal e planilha Google | — | | |
| M10 · Implantação guiada, plantão/rodízio, resultados, relatório WhatsApp | — | | |
| M11 · Páginas de produto, segurança, ouro 100 casos, v1.0.0 | — | | |

---

## M0 · Esqueleto — concluído em 21/11/2026

### Entregue
- Projeto Python 3.12 com dependências fixadas (`uv.lock`), ruff, mypy estrito, pytest com cobertura.
- `app/`: configuração só por ambiente, logs JSON com `tenant_id`/`conversation_id`/`trace_id` e
  mascaramento de dados pessoais, `trace_id` por requisição (`X-Request-ID`), `/health/live` e
  `/health` (Postgres + Redis, 503 sem expor detalhes), processos `worker` e `scheduler` com heartbeat
  no Redis e parada limpa.
- Mascaramento de CPF, CNPJ, cartão, e-mail, chave Pix e telefone (`app/domain/masking.py`, TDD).
- Alembic configurado (papel dono do esquema; sem tabelas até o M1).
- `deploy/`: Dockerfile (não root, dois estágios), `docker-compose.yml` (caddy, app, worker, scheduler,
  postgres, redis, backup, migrate), Caddyfile com HTTPS automático e cabeçalhos de segurança,
  papéis de banco com menor privilégio, `gen-env.sh`, `install.sh`, backup diário cifrado.
- `Makefile` com `up`, `down`, `logs`, `migrate`, `backup`, `lint`, `test`, `security` e os alvos dos
  próximos marcos (avisam em que marco ficam disponíveis).
- CI (GitHub Actions): ruff, mypy, shellcheck, testes com Postgres 16 e Redis 7 reais, migrações ida e
  volta, pip-audit, bandit, semgrep, gitleaks, build da imagem e teste de fumaça (`make up` + `/health`
  pelo Caddy + backup).
- Docs: `DECISIONS.md` (D001–D010), `verificacao-coexistencia.md` + `scripts/capture_webhook.py`,
  `runbook.md` inicial, `CHANGELOG.md`. SPEC: `Mailer` corrigido (sem link mágico).

### Verificado
- `make lint` e `make test` verdes: 51 testes, cobertura 91%.
- Stack completa subiu com `docker compose up --wait`: todos os serviços saudáveis; `/health` respondeu
  200 pelo Caddy em HTTPS com os cabeçalhos de segurança; migração e backup rodaram; o backup decifra e
  o `pg_restore --list` lê o arquivo; falha do `pg_dump` não deixa arquivo.
- `install.sh` num contêiner `ubuntu:24.04` limpo: rejeita `ENV` e domínio inválidos, instala o Docker
  pelo repositório oficial, gera `.env` com permissão 600, não sobrescreve o `.env` ao rodar de novo.
- semgrep (p/python, p/dockerfile, p/secrets), bandit e pip-audit sem achados; gitleaks só acusou o
  `.env` local (ignorado pelo git).

### Limitações conhecidas do ambiente de desenvolvimento
- O Docker do ambiente de desenvolvimento não publica portas. A subida foi validada pela rede interna do
  compose; a publicação das portas 80/443 é verificada pela CI e fica para a VPS.

### Depende de humano
- [ ] Rodar `sudo ./deploy/install.sh` numa **VPS Ubuntu LTS limpa** com domínio apontado (produção) e
      `ENV=staging` numa segunda VPS (homologação). Conferir `https://<domínio>/health`.
- [ ] Executar a **verificação de coexistência** (`docs/verificacao-coexistencia.md`, ~1 h) e registrar
      o resultado em `DECISIONS.md`.
- [ ] Pendências do SPEC, seção 13: conta no BSP, chaves de IA/transcrição, conta de serviço Google,
      revisão jurídica, VPS no Brasil, domínio e armazenamento de backup.

### Fica para os próximos marcos
- Criação do usuário operador no `install.sh` (precisa da tabela `user`: M1; login com TOTP: M7).
- Envio do backup para S3 e `make restore` (M8).
