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

## D016 · `fim` de Tarifas e Bloqueios = dia da saída (M2) — **aguardando confirmação**

- **Contexto:** o SPEC define check-out exclusivo para a estadia, mas não diz como ler o `fim` das
  abas Tarifas e Bloqueios. Muda o que o hóspede vê (qual tarifa vale na última noite; se a casa está
  livre no dia da saída do bloqueio). Pergunta feita ao time no fim do M1.
- **Escolha provisória:** `fim` = dia da saída, exclusivo, igual ao check-out. É a leitura que faz o
  caso de aceite do prompt de partida dar certo ("pacote fechado 28/12–02/01" + busca 28/12–02/01 →
  valor do pacote) e permite períodos encostados (01/12–28/12 e 28/12–02/01) sem lacuna.
- O cabeçalho e o comentário das colunas explicam a regra ("Ex.: 28/12 a 02/01 = noites de 28/12 a
  01/01"). Se o gestor usar a outra leitura, o importador mostra um aviso de "noites sem tarifa" em
  cada emenda, o que torna o engano visível.
- A conversão está num único ponto (`sheet_end_to_exclusive` em `app/importer/validate.py`). Se a
  resposta for "última noite", a troca é somar um dia ali e ajustar o texto do cabeçalho.

## D017 · Avisos não bloqueantes da importação (M2)

- RF14 pede avisos para casa sem tarifa futura, link de fotos fora do ar e capacidade incoerente.
  "Capacidade incoerente" foi traduzida em duas regras conservadoras, só de aviso: mais suítes que
  quartos; mais de 4 hóspedes por quarto.
- Acrescentado: **lacuna entre tarifas** da mesma casa (noites sem tarifa = casa não cotável nessas
  datas, SPEC seção 6). **Sobreposição** de tarifas é **erro**, porque tornaria ambígua a tarifa da
  noite.
- Coluna desconhecida = aviso (ignorada); aba Bloqueios ou Regras ausente = aviso; Imoveis ou
  Tarifas ausente = erro.
- Erros em cascata são evitados: uma casa com erro em outra coluna não faz suas tarifas acusarem
  "código inexistente".

## D018 · Aprovação da planilha (M2)

- Aprovar troca `tenant.active_snapshot_id`, marca a versão anterior como `superseded` e grava quem,
  quando e o hash no `audit_log` (RF15). As versões são numeradas por imobiliária; importações e
  aprovações da mesma imobiliária são serializadas por trava na linha do tenant.
- Os bloqueios da aba Bloqueios ficam guardados na versão e só viram linhas de `block` na aprovação,
  substituindo **apenas** os bloqueios de origem `spreadsheet`. Os de painel, lembrete e iCal ficam.
- Aprovar uma planilha conta como atualização das datas (`availability_updated_at`, RF13): o gestor
  acabou de revisar e aprovar a aba Bloqueios.

## D019 · Leitura de .xlsx: openpyxl + defusedxml, com limites (M2)

- `openpyxl` (leitura e geração, com validação de células) e `defusedxml` (o openpyxl passa a usá-lo
  para o XML, contra ataques de entidade). Dependências leves, puro Python.
- Limites contra arquivo malicioso: 5 MB, 100 MB descompactados, 500 entradas no zip, e linhas por aba
  (Imoveis 1.000, Tarifas e Bloqueios 20.000, Regras 50).

## D020 · Checagem dos links de fotos com proteção contra SSRF (M2)

- A checagem roda **antes** e **fora** da transação do banco. Só http/https nas portas padrão;
  o nome é resolvido e qualquer endereço não público (privado, loopback, link-local, reservado,
  multicast) é recusado; cada redirecionamento (até 3) é conferido de novo; HEAD com recurso a GET.
  O resultado mostrado é só "no ar / fora do ar", sem o conteúdo da resposta.
- Risco residual conhecido: troca de DNS entre a checagem e a conexão (DNS rebinding). Como a
  resposta não é exposta, o impacto se limita a uma requisição cega.

## D021 · Detalhes de `pricing` e `search` não fixados no SPEC (M3)

- **Pacote fechado coberto em parte:** a estimativa (`total_cents`) soma o **pacote completo** (mais as
  diárias das noites fora dele e a limpeza), porque é o que a casa aluga nessas datas. O card mostra as
  datas e o valor do pacote (SPEC); o total só ordena as opções com a mesma flag e entra na soma dos
  valores estimados do relatório.
- **Mínimo de noites:** a flag `below_min_nights` não impede a cotação; o total é o das noites pedidas.
- **Acima do orçamento:** quando nenhuma casa cabe, as "até 3 mais próximas acima" são as de menor total;
  depois seguem a ordem normal do ranking.
- **Dados frescos:** "mais de 24 h" é estrito: 24 h exatas ainda é fresco, 24 h e 1 min é vencido; dado
  nunca atualizado é vencido.
- **Bairro (`neighborhood`):** está no estado da conversa, mas não está entre os filtros obrigatórios
  do SPEC; a busca não filtra por ele. O uso (preferência ou filtro) será tratado na decisão (M5).
- Os tipos `PropertyData`, `RateData` e `BlockData` foram para `app/domain/catalog.py`; o importador e a
  busca usam a mesma definição.

## D022 · Cifra de segredos no banco: AES-256-GCM com rotação (M4)

- **Escolha:** `cryptography` (AESGCM). Chaves em `DATA_ENCRYPTION_KEYS` (`k2:<base64>,k1:<base64>`):
  cifra com a primeira, decifra com qualquer uma (rotação sem migração). O contexto
  (`whatsapp_token:<tenant_id>`) entra como dado associado: um token copiado para outra imobiliária não
  decifra. Usado no token do WhatsApp (M4) e no segredo TOTP (M7).
- `gen-env.sh` gera a chave; **guarde-a fora da VPS** (sem ela os tokens não são recuperáveis).

## D023 · Fila, debounce e worker (M4)

- Implementação da D002 em `app/workers/queue.py`, com scripts Lua atômicos:
  - debounce: prazo = min(última + 6 s, primeira + 20 s), por conversa, num ZSET;
  - streams `jobs:priority` (primeira mensagem de contato novo, RF20) e `jobs:normal`, grupo `workers`;
    o worker lê a prioridade antes; confirma (XACK) só depois de concluir; trabalho não confirmado é
    retomado após 60 s e vai para `jobs:dead` depois de 3 entregas;
  - trava por conversa (um lote por vez); lote que chega no meio é adiado 2 s;
  - anti-abuso: janela deslizante de 60 s por contato; na 16ª mensagem, silêncio de 10 min e **um**
    aviso fixo (`app/domain/messages.py`); mensagens no silêncio são gravadas e não respondidas.
- A ingestão grava no banco **antes** de agendar. O lote leva todas as mensagens de entrada ainda não
  tratadas (`message.processed_at`), na ordem de chegada.
- A transcrição e qualquer chamada externa rodam fora de transação do banco.

## D024 · Autenticação do webhook (M4)

- RF01: `X-Hub-Signature-256` (HMAC-SHA256 do corpo bruto com o app secret) obrigatória; sem segredo
  configurado, todo POST é recusado (falha fechada). Corpo máximo de 1 MB. Erro interno → 500, para a
  Meta reenviar; a idempotência pelo ID externo evita duplicar.
- Se o parceiro (BSP) escolhido não assinar com o app secret da Meta, o modo de autenticação dele entra
  como alternativa configurável, depois da verificação de coexistência (pendência humana).

## D025 · Ecos do app WhatsApp Business (M4)

- Mensagens do campo `smb_message_echoes` (corretor respondendo pelo app) são gravadas como saída com
  origem `business_app`, sem efeito no atendimento. O silêncio automático por eco (RF11) só será ligado
  se a verificação de coexistência passar (SPEC, seção 13, item 3); "responda 1" e o botão "assumir"
  entram no M6 de qualquer forma.

## D026 · Atualização do `.env` entre versões (M4)

- `deploy/gen-env.sh` em um `.env` existente só **acrescenta** as variáveis novas (com segredos
  novos), nunca altera as existentes. `install.sh` o chama sempre, então rodar o instalador de novo
  depois de `git pull` completa o `.env`.

## D027 · Mascaramento de logs não apaga IDs internos (M4)

- Encontrado no teste da stack: o padrão de chave Pix aleatória (UUID) mascarava `tenant_id` e
  `conversation_id`. Chaves de contexto e chaves terminadas em `_id` (geradas pelo sistema) não passam
  pelo mascaramento; texto livre continua mascarado, inclusive UUIDs dentro dele.
