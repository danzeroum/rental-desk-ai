# Progresso

Marcos do SPEC (seção 12). Atualizado ao fim de cada marco.

| Marco | Estado | PR | Versão |
|---|---|---|---|
| M0 · Esqueleto | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.1.0 |
| M1 · Modelo de dados, RLS, isolamento | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.2.0 |
| M2 · Planilha: gerador, importador, snapshot, aprovação | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.3.0 |
| M3 · `pricing` + `search` (TDD) | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.4.0 |
| M4 · Webhook, gateway, debounce, rate limit, transcrição, estado | ⏳ próximo | | |
| M5 · Extração, decisão, templates, validador, auditoria | — | | |
| M6 · Passagem, assumir, escalonamento, pausa, desfecho | — | | |
| M7 · Painel, lembrete 9h, relatório, demo, direitos do titular | — | | |
| M8 · Backup/restore, monitoramento, carga, modo sombra, runbook | — | | |
| M9 · iCal e planilha Google | — | | |
| M10 · Implantação guiada, plantão/rodízio, resultados, relatório WhatsApp | — | | |
| M11 · Páginas de produto, segurança, ouro 100 casos, v1.0.0 | — | | |

---

## M3 · `pricing` + `search` por TDD — concluído em 21/11/2026

### Entregue
- `app/domain/pricing.py`: estadia (check-out exclusivo; check-in = check-out é recusado), cotação noite
  a noite, não cotável sem tarifa, pacote inteiro, pacote fechado parcial (`requires_full_package`),
  pacote aberto parcial em diárias, mínimo de noites pelo maior período tocado (`below_min_nights`),
  limpeza da tarifa do check-in, tudo em centavos.
- `app/domain/search.py`: filtros obrigatórios (capacidade, atributos = Sim, bloqueio só com dados
  frescos, cotável), orçamento com "mais próximas acima", dados vencidos (> 24 h) sem confirmar
  disponibilidade, ranking estável (flags, folga, total, código), no máximo 3.
- Cobertura mínima de 90% exigida em `app/domain` e `app/importer` (`make coverage-core`, na CI).
- Versão 0.4.0.

### Verificado
- 299 testes (2 pulados de propósito); `pricing` 98% e `search` 100% de cobertura; núcleo 97%.
- Limites do CLAUDE.md: período que cruza tarifas, pacote parcial (fechado e aberto), mínimo de noites
  (abaixo, exato, maior entre períodos), check-in = check-out, capacidade exata, dados com 24 h e 1 min,
  bloqueio encostado no check-in/check-out.
- Casos do prompt de partida: "Casa para 12 no Réveillon com piscina" com 2 casas livres de pacote
  fechado → 2 opções com o valor do pacote e sem flags; estadia 30/12–02/01 em casa de pacote fechado →
  `requires_full_package` com as datas do pacote.
- Validação com a planilha de exemplo (Demo Riviera): mesma busca devolve 3 casas na ordem esperada.

### Depende de humano
- Validação do SPEC (seção 10 do PDF): conferir os cálculos com **casos reais feitos à mão por uma
  imobiliária**. Quando vierem, entram como casos de teste.

---

## M2 · Planilha: gerador, importador, snapshot, aprovação — concluído em 21/11/2026

### Entregue
- **Planilha modelo** (`make template`, `python -m app.importer template`): 4 abas com os nomes do SPEC,
  cabeçalho com comentário de ajuda, obrigatórios em vermelho e validação de células (Sim/Não, inteiros,
  valores, datas, código da casa escolhido da aba Imoveis). Regras sugeridas já preenchidas.
- **Exemplo "Imobiliária Demo Riviera"** (`app/importer/sample.py`): 10 casas fictícias, temporada
  completa (baixa, alta, Réveillon, janeiro, Carnaval calculado pela Páscoa, março), metade com pacote
  fechado. Base do modo demonstração (RF25, M7).
- **Importador** (`app/importer/`): leitura defensiva do .xlsx (D019); conversão de células com
  mensagens para o gestor (datas dd/mm/aaaa, valores "R$ 1.500,00" → centavos, Sim/Não); validação por
  aba/linha/coluna; avisos não bloqueantes de casa sem tarifa futura, capacidade incoerente, lacuna de
  tarifa e link de fotos fora do ar (checagem HTTP com proteção contra SSRF, D020).
- **Snapshot e aprovação** (`app/importer/service.py`): rascunho numerado por imobiliária com hash
  SHA-256 do conteúdo normalizado; aprovação registra quem, quando e hash (RF15) no `audit_log`, troca a
  versão ativa e os bloqueios vindos da planilha; rejeição. Nada vai ao ar sem aprovação.
- `python -m app.importer check arquivo.xlsx [--links]` para o suporte conferir um arquivo.
- Migração `0002` (bloqueios do arquivo guardados na versão). Versão 0.3.0.

### Verificado
- `make lint`, `make test` e `make security` verdes: 258 testes (2 pulados de propósito), cobertura
  96,5%; `pip-audit` e `bandit` sem achados.
- Aceite do M2: a planilha de exemplo importa sem erros nem avisos, e o hash bate com os dados de
  origem; uma célula ruim gera exatamente `Tarifas, linha 3, coluna diaria: valor inválido...`.
- Integração: rascunho não vai ao ar; versões incrementam; link quebrado vira aviso; relatório com erro
  não é gravado; aprovação registra quem/quando/hash, troca a versão ativa e só os bloqueios da
  planilha; não aprova duas vezes, nem rejeitada, nem de outra imobiliária.

### Pergunta em aberto
- `fim` de Tarifas e Bloqueios: implementado como **dia da saída** (D016), aguardando confirmação.

### Fica para os próximos marcos
- Tela de envio, correção e aprovação no painel, e o botão "baixar modelo" (M7, RF14/RF15 no painel).
- Planilha Google sincronizada (M9).

---

## M1 · Modelo de dados, RLS, isolamento — concluído em 21/11/2026

### Entregue
- Modelos SQLAlchemy das tabelas da seção 6 do SPEC (`app/models/`) e migração `0001`.
  `user` do SPEC = `app_user` (identidade global) + `tenant_member` (papel por imobiliária) (D011).
  Mais `tenant_route` (roteamento do webhook) e `whatsapp_account` (token cifrado, a partir do M4).
- **RLS ativo e forçado** (`FORCE ROW LEVEL SECURITY`, vale até para o dono) em todas as tabelas com
  `tenant_id` e em `tenant`; sem tenant definido, zero linhas (falha fechada).
- **Filtro na aplicação** além do RLS: `app/core/rls.py` (`tenant_session`, `global_session`) aplica o
  escopo em cada transação e acrescenta `tenant_id` a toda consulta ORM (D015).
- **`audit_log` só de inclusão**, encadeado por hash no banco, uma corrente por tenant, com verificação
  (`audit_log_verify`) e recusa de UPDATE/DELETE/TRUNCATE até para o dono (D014). `privacy_notice`
  imutável. `app/core/audit.py` para incluir e verificar.
- Restrições no banco: dinheiro em centavos (`bigint`, não negativo), período com fim > início,
  código de imóvel em maiúsculas, aprovação registrada (quem/quando) para snapshot aprovado, ID externo
  de mensagem único por tenant (RF01), `state_json` inicial conforme o SPEC.
- Versão 0.2.0.

### Verificado
- `make lint` e `make test` verdes: 119 testes (2 pulados de propósito), cobertura 96%.
- Teste de isolamento (aceite do M1): para **cada** tabela com `tenant_id`, descoberta a partir dos
  modelos, uma consulta SQL **sem filtro** com o tenant A só devolve linhas de A; sem tenant, zero linhas;
  UPDATE/DELETE em linhas de B afetam zero; inserir ou mover linha para B é recusado pelo RLS; um
  `where tenant_id = B` explícito no ORM volta vazio; o papel da aplicação não cria imobiliárias.
- Catálogo: toda tabela do esquema tem `tenant_id` + RLS forçado + política, ou está em
  `GLOBAL_TABLES`.
- Auditoria: encadeamento, campos forjados pelo cliente ignorados, 20 inclusões simultâneas formam uma
  corrente única, adulteração detectada no `seq` exato.
- Migração ida e volta e `alembic check` (modelos = banco) na CI; migração e backup rodaram na stack
  Docker.

### Pergunta em aberto (afeta M2/M3)
- Fim dos períodos de **Tarifas** e **Bloqueios** na planilha: é o dia de saída (exclusivo, como o
  check-out) ou a última noite (inclusivo)? Ver resumo do PR. No banco tudo já é exclusivo; a resposta
  define só a leitura da planilha e o texto do cabeçalho.

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
