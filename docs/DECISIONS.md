# Decisões técnicas

Decisões que não estão no SPEC. Formato: contexto → escolha → alternativas.
A mais recente fica embaixo.

---

## D001 · Gerenciador de dependências: uv com lockfile (M0)

- **Contexto:** o SPEC exige dependências fixadas (OpenSAMM, Implementação) e build reprodutível.
- **Escolha:** `uv` com `pyproject.toml` (faixas de versão) + `uv.lock` (versões e hashes exatos).
  A imagem Docker instala com `uv sync --frozen --no-dev`. O projeto não é empacotado (`package = false`).
- **Alternativas:** pip-tools (mais lento, dois arquivos), Poetry (mais pesado e mais lento no Docker).

## D002 · Fila própria em Redis, sem `arq` (M0)

- **Contexto:** o plano inicial era o `arq`. A versão atual dele exige `redis<6`, e o cliente atual é o
  8.x. Além disso, o debounce do RF05 (reagendar a cada mensagem, máximo de 20 s) e a prioridade para
  contato novo (RF20) não cabem bem no modelo de jobs do `arq`.
- **Escolha:** fila própria sobre **Redis Streams** com grupo de consumidores (entrega ao menos uma vez,
  reprocessamento de mensagens pendentes, idempotência pelo ID externo), implementada no M4. O laço de
  processo, o heartbeat e a parada limpa já ficam prontos no M0 (`app/core/service.py`).
- **Alternativas:** `arq` com `redis<6` (cliente antigo, sem correções de segurança); Celery
  (dependência pesada, síncrona); RQ (síncrona, sem agendamento fino).

## D003 · Driver de banco: psycopg 3 para tudo (M0)

- **Escolha:** `psycopg[binary]` 3 atende o SQLAlchemy assíncrono (app, worker) e o síncrono (Alembic).
  Um driver só, com suporte a `SET LOCAL` por transação, que o RLS usa (M1).
- **Alternativa:** asyncpg + psycopg2 (dois drivers, duas fontes de bug).

## D004 · Cliente HTTP: httpx2 (M0)

- **Contexto:** o `TestClient` do Starlette 1.x passou a usar `httpx2` (sucessor do httpx, mantido pela
  Pydantic) e marca o httpx como obsoleto.
- **Escolha:** `httpx2` para testes e para os adaptadores (WhatsApp, LLM, transcrição) a partir do M4.

## D005 · Logs: structlog em JSON com mascaramento central (M0)

- **Escolha:** `structlog` renderiza JSON e também captura os logs da biblioteca padrão (uvicorn,
  SQLAlchemy). Todo evento tem `tenant_id`, `conversation_id` e `trace_id` (nulos quando não se aplicam).
  Um processador central **descarta** valores de chaves sensíveis (`phone`, `text`, `name`, `body`,
  `token`...) e **mascara** CPF, CNPJ, cartão, e-mail, chave Pix aleatória e telefone em qualquer texto.
  O mascaramento é puro (`app/domain/masking.py`) e será reaproveitado nos prompts (M5).
- O log de acesso do uvicorn fica desligado; o `TraceMiddleware` registra cada requisição com
  `trace_id` e duração. `/health` e `/health/live` vão para DEBUG (Docker, Caddy e o monitor chamam a
  cada poucos segundos).

## D006 · Papéis de banco (M0)

| Papel | Uso | Atributos |
|---|---|---|
| `postgres` | superusuário; só o serviço de backup | — |
| `rentaldesk_owner` | dono do esquema; só migrações | `NOSUPERUSER NOBYPASSRLS` |
| `rentaldesk_app` | caminho de requisição (app, worker, scheduler) | `NOSUPERUSER NOBYPASSRLS` |
| `rentaldesk_ops` | tarefas globais do operador (SPEC, seção 6) | `BYPASSRLS` explícito |

- Criados por `deploy/postgres/init-roles.sh` (idempotente), na primeira subida do Postgres ou na CI.
- Cada contêiner recebe só a senha do papel que usa (o app não conhece a do dono nem a do superusuário).
- O RLS em si (com `FORCE ROW LEVEL SECURITY`, para valer também para o dono) entra no M1.

## D007 · Versão durante o desenvolvimento (M0)

- **Escolha:** `0.<marco+1>.0` até a 1.0: M0 = `0.1.0`, M1 = `0.2.0` ... M10 = `0.11.0`. A tag `v1.0.0`
  só sai com a definição de pronto do SPEC atendida. A versão está em `pyproject.toml` e
  `app/__init__.py`, e aparece no `/health`.

## D008 · Caddy sem e-mail ACME (M0)

- **Contexto:** `email {$ACME_EMAIL}` vazio quebra o Caddyfile, e o Let's Encrypt deixou de mandar
  avisos de expiração por e-mail em 2025.
- **Escolha:** sem e-mail. A renovação é automática; o monitor externo do M8 alerta se o certificado
  estiver perto de vencer.

## D009 · Backup no M0: dump local cifrado (M0)

- **Escolha:** o serviço `backup` já gera, uma vez por dia (`BACKUP_HOUR`), um `pg_dump` cifrado com
  `gpg --symmetric --cipher-algo AES256` em volume próprio, roda como usuário `postgres` (não root),
  apaga arquivos com mais de 30 dias e nunca deixa arquivo truncado (`pipefail` + escrita em `.partial`).
- O envio para armazenamento externo S3 compatível, o `make restore` e a simulação de RTO entram no M8,
  como manda o SPEC.

## D010 · Imagens base e build atrás de proxy (M0)

- Imagem da aplicação: `python:3.12-slim-bookworm`, em dois estágios, usuário sem privilégio
  (uid 10001), sistema de arquivos somente leitura, `cap_drop: ALL` e `no-new-privileges` no compose.
- O Dockerfile aceita um segredo de build opcional `extra_ca` (certificado de proxy corporativo). Sem
  ele, o build é o normal. Não afeta a imagem final.

## D011 · Usuários, vínculos e tabelas globais (M1)

- **Contexto:** o login acontece antes de se saber a imobiliária, e o webhook recebe o
  `phone_number_id` antes de saber o tenant. Com RLS em tudo, essas duas buscas seriam impossíveis.
- **Escolha:**
  - `app_user`: identidade global (e-mail, hash de senha, segredo TOTP cifrado, `is_operator`). Sem
    `tenant_id` e sem dados de negócio. Implementa o `user` do SPEC.
  - `tenant_member`: papel (gestor/corretor) de um usuário numa imobiliária. RLS: lê os vínculos do
    próprio tenant **ou** do usuário logado (`app.current_user_id`); escreve só no próprio tenant.
  - `tenant_route`: `phone_number_id` → tenant. Leitura global (só identificadores públicos, sem token);
    escrita só no próprio tenant.
  - `tenant`: RLS por `id`. O papel da aplicação **não insere** imobiliárias (só o operador, com
    `rentaldesk_ops`) e só altera as colunas de configuração.
  - A lista `GLOBAL_TABLES` (`app/models/__init__.py`) é a única exceção aceita pelo teste de
    isolamento; tabela nova sem `tenant_id` fora dessa lista quebra o teste.
- **Alternativas:** função `SECURITY DEFINER` para o login e o roteamento (esconde a regra dentro do
  banco e exige dono com `BYPASSRLS`); `user` com `tenant_id` (gestor de duas imobiliárias precisaria de
  duas contas).

## D012 · Valores fechados como texto + CHECK (M1)

- **Escolha:** status, papéis, origens e desfechos são `varchar` com `CHECK (col IN (...))`, gerados a
  partir de `StrEnum` (`app/models/enums.py`).
- **Alternativa:** `ENUM` do Postgres (acrescentar valor exige migração especial e não roda em
  transação).

## D013 · Snapshot de dados e bloqueios (M1)

- **Escolha:** imóveis e tarifas pertencem a um `data_snapshot` imutável; `tenant.active_snapshot_id`
  aponta para o aprovado em uso (RF15). Aprovar = trocar o ponteiro, numa transação. Bloqueios **não** são
  versionados (mudam pelo painel, pelo lembrete das 9h e pelo iCal, sem aprovação — RF16, RF26, RF27) e
  referenciam o imóvel pelo `codigo`, estável entre versões.
- No banco, todo período (estadia, tarifa, bloqueio) é guardado como `start_date` ≤ noite < `end_date`
  (fim exclusivo). Como a planilha expressa o fim de tarifas e bloqueios é decidido na importação (M2).

## D014 · Auditoria encadeada no banco, por tenant (M1)

- **Escolha:** gatilho `BEFORE INSERT` em `audit_log` calcula `seq`, `prev_hash`, `created_at` e
  `hash = sha256(prev_hash ‖ campos canônicos)`, com trava consultiva por tenant (a corrente não
  bifurca com inclusões simultâneas). Valores enviados pelo cliente para esses campos são ignorados.
  Uma corrente por tenant: imobiliárias não disputam a mesma trava.
- UPDATE e DELETE: sem permissão para os papéis de execução **e** recusados por gatilho até para o
  dono do esquema; TRUNCATE também. `audit_log_verify(tenant)` devolve o primeiro `seq` adulterado.
- `audit_log` não tem chave estrangeira para `conversation`: a trilha sobrevive à anonimização (RF21).
  O conteúdo gravado não terá nome nem telefone (SPEC, seção 7, item 8), então a trilha pode ser
  mantida pelo prazo legal sem impedir o direito de exclusão.
- `privacy_notice` também é imutável por versão.

## D015 · Filtro por tenant também na aplicação (M1)

- **Escolha:** sessões criadas por `scoped_session_factory` aplicam `set_config(..., true)` em toda
  nova transação (evento `after_begin`) e acrescentam `tenant_id = <atual>` a toda consulta ORM sobre
  modelos `TenantScoped` (`with_loader_criteria`). Mesmo um `where` explícito para outro tenant volta
  vazio. Sem tenant, a política devolve zero linhas (falha fechada).
