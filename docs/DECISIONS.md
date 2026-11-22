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
  O mascaramento é puro (`app/core/masking.py` (movido do `domain` no M8: o core não importa o domínio)) e será reaproveitado nos prompts (M5).
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

## D016 · Períodos na planilha: colunas sem ambiguidade (M2, revisado em 22/11/2026)

- **Contexto:** a v1 da planilha usava `inicio`/`fim` em Tarifas e Bloqueios, e não ficava claro se
  `fim` era a última noite ou o dia da saída; a diferença muda o preço e a disponibilidade.
- **Decisão do time:** manter a regra interna (noite de entrada incluída, dia de saída excluído) e
  trocar os nomes das colunas:
  - Tarifas: `primeira_noite` e `ultima_noite`, as duas **incluídas**. O importador converte para o
    banco com `fim = ultima_noite + 1 dia` (`rate_period` em `app/importer/validate.py`).
  - Bloqueios: `entrada` e `saida` do hóspede; `saida` já é o dia em que a casa fica livre, sem
    conversão.
- Um período de tarifa de uma noite só é válido (`primeira_noite = ultima_noite`). Bloqueio com
  `saida` igual ou antes da `entrada` é erro.
- Mensagens de sobreposição e lacuna falam em noites ("noites de 28/12/2026 a 28/12/2026 sem tarifa").
- SPEC, seção 8, e a central de ajuda (`docs/ajuda/planilha.md`) atualizados.

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

## D028 · Provedor de LLM: SDK oficial da Anthropic, modelo por ambiente (M5)

- **Escolha:** `LLMClient` com um único método, `complete_json(system, user, schema)`, usado na extração
  e na redação. Implementação `AnthropicLLMClient` com o SDK oficial `anthropic` (2.12, já sobre
  `httpx2`), `output_config.format` (JSON com esquema) e `output_config.effort` (padrão `low`, pela meta
  de p95 ≤ 15 s). `LLM_MODEL` vem do ambiente; o `.env` gerado sugere `claude-opus-5-5`, e a escolha
  final é feita pelo conjunto de ouro com o LLM real (SPEC, 0b).
- **Recusa de segurança:** para os modelos que aceitam, o pedido vai com o fallback de recusa no
  servidor (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`), que repete o pedido em outro
  modelo se o primeiro recusar. Desligável com `LLM_REFUSAL_FALLBACK=false`. Se ainda assim vier
  recusa, ou a saída for cortada (`max_tokens`), ou houver erro de rede/API, vale a contingência do SPEC
  (frase fixa + passagem).
- **Revisão de 22/11/2026 (decisão do time):** o `.env` gerado passa a sugerir `claude-haiku-5-5`.
  `make golden-live` compara `claude-haiku-5-5`, `claude-sonnet-5-5` e `claude-opus-5-5` no conjunto de
  ouro e grava `docs/golden/comparacao-AAAA-MM-DD.md` com acertos, critérios de produção, latência p95
  por volta e custo estimado por conversa. Fica o **mais barato aprovado em todos os critérios do SPEC
  (seção 11) com p95 ≤ 15 s**. O fallback de recusa continua ligado, e cada acionamento fica no
  `audit_log` da resposta (`llm_refusal_fallback`: modelo que recusou e modelo que respondeu), junto da
  lista de chamadas ao LLM (`llm_calls`: modelo, tokens, latência). Esses dados também servem à meta de
  custo do SPEC (seção 5).
- `FakeLLM` por regras, determinístico, para `make golden` e desenvolvimento. Outros provedores entram
  como novas implementações da mesma interface, se o conjunto de ouro indicar.
- Pendência humana: chave do provedor com contrato sem uso dos dados para treino.

## D029 · Motor de conversa separado do pipeline (M5)

- `app/workers/engine.py` faz uma volta da conversa sem banco (extração → decisão → busca → texto); o
  conjunto de ouro roda direto nele. `app/workers/pipeline.py` carrega o contexto do banco, chama o
  motor, envia e grava (estado, `intent`, `search_result`, `handoff`, `audit_log`).
- O LLM escreve **só** a abertura e o fechamento das respostas com casas (uma chamada, JSON com as duas
  frases). Perguntas, respostas de atributo, passagem, contingência e avisos são textos fixos do código.
- O `audit_log` de cada resposta guarda hash do snapshot, JSON da extração, decisão, consulta e
  resultado da busca, resultado do validador e texto final (SPEC, seção 7, item 9).
- Modo sombra: imobiliária com status `shadow` tem a resposta registrada (`shadow_response`) e não
  enviada. Imobiliária em implantação ou suspensa não é atendida.
- As mensagens do lote só são marcadas como tratadas depois do envio; falha no envio deixa o lote para
  nova tentativa (e, depois de 3, para a fila morta).

## D030 · Regras do validador e da decisão não fixadas no SPEC (M5)

- Validador: além do SPEC (dígitos, números por extenso, mês com dia, códigos, palavras proibidas), recusa
  "R$"/"reais", links e frases com mais de 220 caracteres; "um"/"uma" são permitidos (artigos); palavras
  proibidas incluem flexões (confirmo, garanto, descontos).
- Decisão: cumprimento no começo pergunta o que falta sem contar como tentativa; agradecimento depois das
  opções responde "fico à disposição" em vez de repetir a busca; pergunta sobre casa não identificada
  pede a referência e conta como tentativa; datas passadas são perguntadas de novo; JSON fora do esquema
  conta como tentativa (RF03). Duas tentativas → passagem (SPEC, seção 7, item 6).
- Sem resultado: texto fixo sugerindo mudar datas, pessoas ou itens, ou falar com um corretor (sem
  passagem automática).
- O aviso de dados vencidos foi escrito sem a palavra "confirmadas" (o detector de afirmação de reserva
  do conjunto de ouro acusou o texto antigo).

## D031 · Conjunto de ouro (M5)

- `tests/golden/cases/*.yaml`: cada caso é uma conversa contra a imobiliária de exemplo; expectativas por
  volta (ação, passagem, casas, trechos) e critérios automáticos em toda resposta: nenhum valor, data,
  código ou mínimo de noites fora do resultado da busca; aviso em todos os cards; nenhuma afirmação de
  reserva; dado pessoal da mensagem nunca vai sem máscara ao LLM.
- `make golden` (FakeLLM, na CI) exige todos os casos; `make golden-live` (LLM real) exige os critérios
  de produção do SPEC (seção 11). 41 casos no M5 (21 fluxos, 20 adversários); 100 até o M11.

## D032 · Horário dos corretores e tempo comercial (M6)

- `tenant.broker_hours`: dia da semana (0 = segunda) → turnos `["HH:MM", "HH:MM"]`, fim exclusivo, fuso
  America/Sao_Paulo; aceita turno partido. Validado em `app/domain/hours.py`.
- Enquanto a imobiliária não configurar (assistente de implantação, M10), vale **todos os dias,
  9h às 18h**. Horário inválido salvo no banco cai no padrão, com log de aviso.
- Os prazos do RF12 contam **tempo comercial**: fora do horário os relógios param, e a passagem criada à
  noite começa a contar na abertura do plantão (`handoff.clock_started_at`).

## D033 · Passagem ao corretor na prática (M6)

- **Aviso por template:** o aviso sai do número da imobiliária para o WhatsApp pessoal do corretor, fora de
  qualquer janela de 24 h; por regra da Meta, só por template aprovado (`docs/whatsapp-templates.md`,
  pendência humana). Só recebe quem tem `consent` ativo para `handoff_alert` no mesmo telefone.
- **Rodízio:** nova passagem vai para quem recebeu passagem há mais tempo (ou nunca); a cada 10 min
  comerciais sem assumir, vai para o próximo da lista, em ciclo (sozinho no plantão, recebe de novo). A
  escala por dia e horário de cada corretor (RF29) entra no M10; até lá, "de plantão" = corretores com
  WhatsApp e consentimento, dentro do horário da imobiliária.
- **"Responda 1":** a resposta do corretor chega ao webhook como mensagem de um contato; se o telefone é de
  um corretor ou gestor cadastrado, a mensagem vira comando e não entra no fluxo de hóspede (vale mesmo com
  o assistente pausado). "1", "assumir" ou "assumo" assumem a passagem aberta mais recente atribuída a ele
  (gestor: qualquer passagem aberta); qualquer outra mensagem recebe uma orientação curta. A confirmação
  inclui o telefone do hóspede, para o corretor achar a conversa no app.
- **Assumir** (WhatsApp, painel ou eco): passagem `assumed`, conversa `human` e muda por 24 h; segunda
  tentativa de assumir recebe "já foi assumida por outra pessoa".
- **Sem corretor de plantão:** a passagem fica aguardando; o alerta dos 15 min ao gestor e ao operador
  cobre o caso.
- **Alerta dos 15 min:** gestor por template `alerta_passagem`; operador por e-mail (`Mailer`, SMTP).
- **Mensagem dos 60 min ao hóspede:** texto fixo "um corretor vai falar com você em breve", com o telefone
  de contato da imobiliária quando configurado (RF24). Enviada uma vez; marcada antes do envio, para não
  repetir se o envio falhar.
- **Desfecho:** fechou ou perdeu encerram a passagem (`closed`); pendente mantém o estado.
- **Eco do app (RF11):** implementado atrás de `WHATSAPP_ECHO_MUTE`, **desligado por padrão**; ligar só
  depois da verificação de coexistência.

## D034 · Scheduler e papel de operação (M6)

- `python -m app.scheduler` roda tarefas por intervalo, cada uma sob trava no Redis. A lista de
  imobiliárias com passagem aberta usa o papel `rentaldesk_ops` (BYPASSRLS); `DATABASE_OPS_URL` existe
  **só** no contêiner do scheduler. O trabalho em si roda por imobiliária, com o papel da aplicação e RLS.

## D035 · Acesso ao painel (M7)

**Contexto:** SPEC seção 5 — e-mail + senha + TOTP obrigatório para todos; sem link mágico.

- **Dependências novas (leves, sem dependências nativas além do argon2):** `argon2-cffi` (hash de
  senha argon2id, recomendação OWASP), `segno` (QR em SVG, puro Python), `python-multipart`
  (formulários no FastAPI). **HTMX 2.0.4** copiado para `app/web/static/` (sem CDN: a CSP é `'self'`;
  integridade conferida com o `sha512` do npm). Alternativas descartadas: `passlib` (sem manutenção),
  `pyotp` (TOTP são 15 linhas; testado com os vetores da RFC 6238), `qrcode` + Pillow (pesado).
- **Sessão:** token aleatório no cookie `rd_session` (`HttpOnly`, `Secure`, `SameSite=Lax`); no Redis só
  o SHA-256, com expiração por inatividade de 8 h. Estágios `awaiting_totp` → `active` (ou `totp_setup` no
  primeiro acesso). Trocar a senha encerra todas as sessões da pessoa.
- **Força bruta:** 5 senhas erradas bloqueiam a conta por 15 min; 30 tentativas por IP em 10 min; 5
  códigos TOTP errados encerram a sessão. A mensagem de erro de login é a mesma exista ou não o e-mail,
  e a conta inexistente também paga um hash (tempo parecido). Código TOTP aceito ±1 passo e nunca duas
  vezes.
- **CSRF:** duplo envio (cookie `rd_csrf` + campo `csrf` ou cabeçalho `X-CSRF-Token` do HTMX, posto no
  `<body hx-headers>`), mais conferência do `Origin`. CSP sem `unsafe-inline`; nada de estilo ou script
  inline nos templates.
- **Convite e nova senha:** links de uso único (72 h e 1 h). A senha nova é validada antes de consumir o
  token, e o consumo é atômico (`GETDEL`). Pedido de nova senha responde igual exista ou não o e-mail.
- **Operador:** `python -m app.cli create-operator` (o `install.sh` pergunta o e-mail no fim) imprime o
  convite. Perdeu o celular: `make reset-totp EMAIL=...` apaga o autenticador e encerra as sessões.

## D036 · Painel do gestor (M7)

- **Papéis no painel:** gestor administra tudo da imobiliária; corretor vê o início, as passagens (pode
  assumir), as datas (pode bloquear e confirmar) e "Meus avisos"; não envia planilha, não muda
  configuração, não marca desfecho. Operador entra em qualquer imobiliária como **suporte**, com o
  evento `support_access` no `audit_log`. O papel fica na sessão; remover alguém da equipe encerra as
  sessões dessa pessoa.
- **Consentimento para avisos ativos:** dado pela própria pessoa em "Meus avisos" (telefone +
  finalidades + concordância explícita), com evidência "aceite no painel pelo próprio usuário, texto
  v1". O gestor só vê a situação. Trocar o telefone ou desmarcar revoga (`revoked_at`); remover da
  equipe revoga tudo. Alternativa descartada: gestor marcar o consentimento pelo corretor (não prova
  vontade do titular).
- **Horário dos corretores no formulário:** um turno por dia (início/fim; vazio = sem atendimento). O
  domínio aceita vários turnos; a tela com mais de um turno fica para o RF29 (escala, M10).
- **Bloqueios pelo painel:** colunas "entrada" e "saída" como na planilha (D016: a noite da saída fica
  livre). Só bloqueios do painel e do lembrete podem ser liberados no painel; os da planilha e do
  calendário se alteram na origem. Bloquear não renova a validade das datas: o botão "As datas abaixo
  estão certas" faz isso (RF16), como o "OK" do lembrete.
- **Planilha:** até 5 MB; com erro nada é gravado; sem erro vira rascunho e abre a conferência (casas,
  faixa de diária, avisos, casas novas e que saem); só "Aprovar" põe no ar (RF15).
- Endereço malformado no painel (id inválido) responde 404 em HTML; a API mantém o 422 em JSON.

## D037 · Painel do operador sem BYPASSRLS (M7)

**Contexto:** RF23 pede a saúde de todas as imobiliárias numa tela; CLAUDE.md proíbe papel com BYPASSRLS
no caminho de requisição.

- **Opção escolhida:** migração 0005 cria `app_current_user_is_operator()` e a política
  `tenant_operator_read` (só SELECT em `tenant`) para o usuário operador ativo identificado em
  `app.current_user_id`. As métricas de cada imobiliária são lidas com `tenant_session` e as políticas de
  sempre. Nenhuma outra tabela de negócio ganhou leitura cruzada.
- **Alternativas descartadas:** usar `rentaldesk_ops` no processo `app` (viola a regra); tabela global de
  saúde escrita pelo scheduler (mais uma cópia de dado, atrasada).
- **Saúde (`app/domain/health.py`):** erro = fila de falhas, mensagem sem processar há 10 min, número
  vermelho, imobiliária ativa sem planilha aprovada; alerta = pausado, número amarelo, datas vencidas,
  passagem aguardando há mais de 15 min, frase recusada pelo validador em 24 h, nenhuma mensagem em
  48 h. Qualidade do número: o scheduler consulta `quality_rating` na Graph API a cada hora (o evento de
  qualidade da Meta não traz `phone_number_id`).
- **Criar imobiliária** é tarefa global: `make tenant NAME=... EMAIL=...` roda no contêiner do scheduler
  (único com `DATABASE_OPS_URL`), cria a imobiliária em `onboarding`, a rota por slug (canal `slug` em
  `tenant_route`, para o link público do aviso de privacidade), o aviso v1 padrão
  (`app/domain/privacy.py`, **texto a validar com advogado**) e convida o gestor.
- **Suporte:** o operador entra em qualquer imobiliária pelo botão "Entrar como suporte"; cada entrada
  grava `support_access` no `audit_log` da imobiliária.

## D038 · Lembrete das 9h e relatório semanal (M7)

- **Lembrete (RF16):** template `lembrete_datas` só a gestor com consentimento `daily_reminder` e
  telefone igual ao do consentimento. Sai uma vez por dia e imobiliária (trava no Redis com a data
  local), entre 9h e 18h: se o scheduler estava parado às 9h, sai quando voltar, mas nunca à noite.
  Imobiliária de demonstração e as que não estão `active` não recebem.
- **Resposta ao lembrete:** vale de qualquer membro da equipe com o WhatsApp cadastrado (o painel
  também deixa o corretor confirmar). "OK" renova a validade das datas; "CASA12 28/12-02/01"
  bloqueia com origem `reminder` (saída exclusiva, D016; ano opcional, saída antes da entrada = ano
  seguinte; aceita "a", "até" e o travessão que o teclado do celular põe). Bloquear não confirma o
  resto: a resposta pede o "OK". Mensagem de membro que não é comando recebe a lista de comandos.
- **Relatório (RF18):** segunda, 8h-18h, semana anterior (segunda a segunda, fuso de São Paulo).
  Gravado em `weekly_report` (único por semana) e enviado por **e-mail** aos gestores ativos — é
  comunicação do serviço contratado, sem dado de hóspede. Métricas: contatos distintos, % que chegou
  fora do horário dos corretores, primeira resposta (mediana e p95, inclui o debounce), passagens,
  desfechos, taxa de fechamento = fechou / (fechou + perdeu), mediana até assumir (minutos de
  relógio) e soma dos totais das opções enviadas. Sem dado = "—", nunca zero inventado. O e-mail traz
  o link "um clique por desfecho" (RF17) quando há passagem sem desfecho.

## D039 · Direitos do titular, aviso de privacidade e modo demonstração (M7)

- **PARAR (RF21):** a mensagem inteira igual a "parar", "pare", "stop", "sair" ou "descadastrar" (não
  "parar na praia") grava `contact.opted_out_at`, responde uma confirmação fixa e, dali em diante, nada
  automático sai para o número — nem o aviso de espera dos 60 min (o escalonamento não falha por isso).
  Vale com o assistente pausado. **VOLTAR** reativa: sem isso, um "PARAR" por engano deixaria o hóspede
  sem atendimento para sempre (prática comum em SMS, "START"). *Esclarecimento a confirmar com o time.*
- **Exportar e apagar por telefone:** só gestor (ou suporte), no painel; o telefone só trafega em
  formulário POST. Exportar entrega JSON legível (mensagens, transcrições, passagens, extrações e a lista
  de eventos de auditoria). Apagar remove o contato e, em cascata, conversas, mensagens (com referências
  de anexo), extrações, buscas e passagens, e troca o telefone por "[apagado a pedido]" nas confirmações
  enviadas a corretores. O `audit_log` **fica** (imutável, prazo legal): não guarda telefone, nome nem
  texto do hóspede.
- **Correção:** a confirmação ao corretor ("Você assumiu… hóspede +55…") ia inteira para o `audit_log`,
  que não pode ser apagado; agora o `audit_log` grava a versão com `[TELEFONE]` (`send_reply(audit_text=)`).
- **Aviso de privacidade público:** `/privacidade/<slug>[/v<n>]`. A imobiliária é achada pela rota de
  canal `slug` em `tenant_route` (leitura global já existente), sem afrouxar o RLS de `tenant`.
- **Modo demonstração (RF25):** `make seed-demo` cria ou renova a "Imobiliária Demo Riviera" (ativa,
  `is_demo`, temporada corrente aprovada pelo gestor fictício `gestor-demo@rentaldesk.invalid`, domínio
  reservado que não recebe e-mail). Com `PNID` e o token em `DEMO_WHATSAPP_TOKEN` no shell, liga o número
  de teste da Meta (token cifrado no banco). Demonstração não recebe lembrete nem relatório.
- **Painel:** datas sempre no fuso de São Paulo (filtro `local`); o estilo embutido do HTMX fica
  desligado (`htmx-config`) para manter a CSP sem `unsafe-inline`. Resposta de atributo sem artigo
  ("Sobrado Azul (CASA03): piscina, não."), porque o título da casa pode ser masculino.

## D040 · Ajustes do time sobre o M7 (PARAR, e-mail semanal, auditoria das datas)

- **PARAR:** uma única confirmação, com o texto do time ("Pronto, não enviaremos mais mensagens. Se
  quiser voltar a falar com a gente, escreva VOLTAR."). Depois, nada automático sai para o número: nem
  IA, nem lembrete, nem template (o filtro fica em `brokers._with_consent`, o ponto único de quem recebe
  aviso ativo). PARAR vale também para corretor e gestor e vem antes de qualquer comando ("1", "OK").
  Só o próprio titular reativa, escrevendo VOLTAR; o painel mostra "saiu (PARAR)" nas passagens e na
  equipe, sem botão de reativar. PARAR e VOLTAR entram no `audit_log` com `command` e `at`.
- **Relatório semanal por e-mail:** aprovado como comunicação do serviço contratado, só com números
  agregados. O gestor desliga em "Meus avisos", sem pedir aceite (`tenant_member.weekly_email_off_at`,
  migração 0006; mudança auditada).
- **Datas:** corretor pode confirmar e bloquear. Cada confirmação, bloqueio e liberação grava no
  `audit_log` quem (`user_id`), por onde (`via`: `panel` ou `reminder`) e quando (`at`). O lembrete das
  9h continua só para o gestor.

## Pontos de extensão para outros segmentos

Anotações para depois da Versão 1.0 (o produto segue focado em temporada; nada disto foi refatorado).

- **Regra de dependência:** `app/core` não importa `app/domain`. Violação encontrada e corrigida no M8:
  `app/core/logging.py` importava `app/domain/masking.py`; o mascaramento passou para `app/core/masking.py`.
- **Peças genéricas fora do core:** mensageria (gateway, responder), fila e debounce, passagem e
  escalonamento, opt-out e consentimento vivem em `app/workers/` e `app/adapters/`, não em `app/core/`.
  Não importam o domínio de imóveis diretamente, exceto: `workers/engine.py` e `workers/pipeline.py`
  (orquestram busca/cards de temporada) e `workers/reminders.py` (códigos de casa no lembrete). Um
  segmento novo trocaria o motor (`engine`) e o lembrete; a fila, o webhook, a passagem e a auditoria
  servem como estão.
- **Domínio com peças genéricas:** `domain/hours.py` (horário comercial), `domain/rotation.py`
  (rodízio), `domain/phones.py`, `domain/messages.py` (PARAR/VOLTAR) e `domain/health.py` não dependem
  de imóveis e poderiam subir para o core quando houver segundo segmento.
- **Pousadas:** usam o mesmo domínio (unidade, capacidade, tarifa por noite, bloqueio), trocando
  "casa/imóvel" por "quarto". O rótulo da unidade aparece em textos fixos (`domain/render.py`,
  `domain/notices.py`, `domain/reminder.py`), na planilha modelo (`importer/schema.py`) e nos templates
  do painel. **Avaliação:** pode virar configuração do tenant (`unit_label` singular/plural, com gênero
  para os artigos) passada ao `render`; os templates aprovados na Meta precisariam de uma versão por
  rótulo (ou de um parâmetro). Não implementado.
- **Tenant próprio (M12):** pacote de perguntas e respostas sem disponibilidade nem preço; exige um
  motor alternativo ao `engine` de temporada, escolhido por tenant.

## D041 · Operação: backup, alertas, capacidade, falhas e modo sombra (M8)

- **Backup:** `pg_dump` em formato custom **com dono e permissões** (antes era `--no-owner`: a
  restauração deixaria as tabelas com o superusuário e as migrações do papel dono falhariam),
  cifrado com gpg. Cópia externa por `rclone` para qualquer S3 compatível, **desligada** sem
  `BACKUP_S3_*` (pendência humana: conta de armazenamento); sem ela o painel do operador alerta
  "sem cópia externa". Sinal de vida em `rd:heartbeat:backup` (Redis) para os alertas.
- **Restauração:** sempre num banco à parte, com conferência (tabelas e `audit_log_verify` de cada
  imobiliária) antes de trocar; o banco antigo fica renomeado, não apagado. O mesmo script faz o
  exercício (sem troca). Alternativa descartada: `pg_restore --clean` direto no banco em uso (sem volta
  se o arquivo estiver ruim).
- **Alertas:** scheduler a cada 5 min; um e-mail por rodada aos operadores com os alertas novos; o mesmo
  alerta (chave + texto) não repete por 12 h. Monitor externo do `/health` fica com o operador
  (serviço de terceiros, pendência humana). Sem webhook de chat por enquanto.
- **Capacidade:** o worker roda `WORKER_CONCURRENCY` laços (padrão 40) e lê um trabalho por vez por
  laço; conexões do banco = metade dos laços + 2. Medido em `docs/carga.md`. Postgres com
  `max_connections=200`.
- **Recuperação de mensagens:** varredura a cada minuto reagenda conversas com mensagem de hóspede não
  processada há mais de 1 min (cobre Redis fora entre gravar e agendar, quando o reenvio da Meta é
  descartado como duplicado). O worker não cai com Redis/Postgres fora: espera (até 10 s) e tenta.
- **Modo sombra:** não cria passagem real nem avisa corretor (antes criava); a passagem que o
  assistente faria fica no registro. Transições implantação → sombra → atendendo (e volta) no painel,
  só gestor/suporte, com planilha aprovada e número conectado; ativar antes de 2 dias exige confirmação.
- **Fila de falhas** limitada a 10 mil entradas (antes crescia sem limite).

## D042 · Calendários iCal e planilha Google (M9)

- **iCal (RF26):** leitor próprio (`app/domain/ical.py`, puro, testado com exportações do Airbnb e com
  data/hora e fuso): só VEVENT com UID/DTSTART/DTEND, ignora cancelados. Alternativa descartada: a
  biblioteca `icalendar` (dependência a mais para o pouco que usamos). Dia inteiro já tem fim
  exclusivo; com hora, vale a data local de São Paulo do início e do fim.
- **Endereço do calendário cifrado** (o do Airbnb leva token); o painel mostra só o domínio. Leitura com
  a mesma proteção contra SSRF do verificador de links (http(s), porta padrão, endereço público a cada
  redirecionamento), até 2 MB e 15 s. Limitação conhecida: a checagem de DNS e a conexão são dois
  passos (rebinding); aceitável porque o conteúdo vira só datas.
- **Bloqueios do link** são trocados a cada leitura (`block.calendar_feed_id`; remover o link remove os
  bloqueios). Só eventos que terminam depois de hoje.
- **Validade das datas (RF13) pelo iCal:** só quando **toda** casa da planilha aprovada tem calendário
  e todos foram lidos com sucesso nos últimos 30 min. Com casa sem calendário, o "OK" diário continua
  necessário (renovar parcialmente diria "livre" para uma casa não conferida).
- **Planilha Google (RF27):** conta de serviço (`GOOGLE_SERVICE_ACCOUNT_JSON`, pendência humana), token
  por JWT RS256 com `cryptography` (já era dependência). Sem a variável, a ligação aparece desligada no
  painel. A planilha é exportada como .xlsx e passa pelo mesmo importador da planilha modelo.
- **O que entra direto e o que pede aprovação:** compara o hash do catálogo (casas, tarifas, regras,
  sem bloqueios) com o da versão no ar. Igual → só bloqueios mudaram → entram direto (origem
  `google_sheet`), sem renovar a validade das datas (mexer na planilha não é conferir as datas). Diferente
  → rascunho (origem `google_sheet`) que espera aprovação, com e-mail ao gestor; rascunho igual
  pendente não é duplicado. A aprovação troca os bloqueios de planilha das duas origens (upload e Google):
  a planilha aprovada é a fonte única deles.
- **Falhas:** 3 seguidas (link fora, planilha com erro, não compartilhada) → e-mail ao gestor uma vez e
  alerta na saúde da imobiliária (operador); um sucesso zera.

## D043 · Implantação guiada, plantão, resultados e resumo por WhatsApp (M10)

- **Plantão (RF29):** `tenant_member.duty_hours` (mesmo formato do horário da imobiliária; vazio =
  segue o horário da imobiliária). O rodízio só considera quem está de plantão na hora do aviso; fora
  do horário da imobiliária nada dispara (RF12). Escala inválida não tira o corretor do rodízio (a
  imobiliária não pode ficar sem ninguém por erro de cadastro).
- **Resultados (RF30):** a mesma conta do relatório semanal (`collect_period`), em hoje, 7 e 30 dias.
  Números em blocos de destaque; gráfico de barras de uma série (contatos por dia) desenhado no
  servidor em SVG, cor `--serie` validada nos dois modos (o azul de destaque do modo escuro falhava na
  faixa de luminosidade), dica nativa por barra e tabela equivalente. Sem biblioteca de gráficos e sem
  JavaScript (CSP).
- **Resumo por WhatsApp (RF32):** template `resumo_semanal` de 5 linhas, só para gestor com
  consentimento `weekly_report` e que não pediu PARAR; o e-mail continua.
- **Implantação (RF28):** os 7 passos são calculados do estado real (número, versões, simulador,
  situação), então o progresso fica salvo sem tabela própria e o operador vê o mesmo passo.
  - Passo 1: o cadastro do número no parceiro (embedded signup em coexistência) é feito no site do
    parceiro (pendência humana: conta no BSP); no painel o gestor informa ID do número, telefone e
    token, e o sistema **testa a conexão antes de salvar**. Número já ligado a outra imobiliária é
    recusado.
  - Passo 4: simulador com o mesmo motor e contexto do WhatsApp (`build_turn_context`), estado no
    Redis por 2 h, nada enviado; cada mensagem gera `simulator_used` (sem texto) para o progresso.
  - Passo 6 → 7: ativar exige ao menos uma resposta real registrada no modo sombra.
  - Imobiliária já atendendo conta como implantação concluída, mesmo sem ter usado o simulador.
- **O processo `app` passa a ter um cliente de IA** (só para o simulador); as chaves são as mesmas do
  worker.
