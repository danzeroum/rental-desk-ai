# Progresso

Marcos do SPEC (seção 12). Atualizado ao fim de cada marco.

| Marco | Estado | PR | Versão |
|---|---|---|---|
| M0 · Esqueleto | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.1.0 |
| M1 · Modelo de dados, RLS, isolamento | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.2.0 |
| M2 · Planilha: gerador, importador, snapshot, aprovação | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.3.0 |
| M3 · `pricing` + `search` (TDD) | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.4.0 |
| M4 · Webhook, gateway, debounce, rate limit, transcrição, estado | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.5.0 |
| M5 · Extração, decisão, templates, validador, auditoria | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.6.0 |
| M6 · Passagem, assumir, escalonamento, pausa, desfecho | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.7.0 |
| M7 · Painel, lembrete 9h, relatório, demo, direitos do titular | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.8.0 |
| M8 · Backup/restore, monitoramento, carga, modo sombra, runbook | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.9.0 |
| M9 · iCal e planilha Google | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.10.0 |
| M10 · Implantação guiada, plantão/rodízio, resultados, relatório WhatsApp | ✅ concluído | danzeroum/rental-desk-ai#1 | 0.11.0 |
| M11 · Páginas de produto, segurança, ouro 100 casos, v1.0.0 | ✅ concluído (tag pendente de itens humanos) | danzeroum/rental-desk-ai#1 | 1.0.0rc1 |
| Kit de demonstração para vendas (entre a rc e a v1.0.0) | ✅ concluído | PR próprio, após o merge do #1 | entra na próxima versão |
| M12 · Tenant próprio e demos por segmento | ⏸ só depois da `v1.0.0` | — | — |

---

## Kit de demonstração para vendas — concluído em 22/11/2026

### Entregue
- **Simulador com aparência de WhatsApp** em `/demo`, usando o pipeline real (webhook interno →
  debounce → worker → motor → cartões → passagem). Funciona no celular, com as telas empilhadas e
  sem rolagem lateral. Mostra "digitando…", sugestões de pedido e o botão "Recomeçar".
- **Celular do corretor** ao lado: aviso de passagem (template preenchido) e botão "Responder 1", que
  assume a conversa pelo mesmo caminho do WhatsApp.
- **`DemoGateway` + `RoutingGateway`:** números `demo-` gravam no Redis; os outros números não mudam.
- **Duas imobiliárias fictícias** em `make seed-demo`: Maré Alta (8 casas) e Pousada Recanto da Enseada
  (6 quartos). Plantão 24 h, corretor fictício com consentimento.
- **Links com token** criados e revogados em `/operador/demo` (migração 0009, tabela global
  `demo_link` com o hash). O token vira um cookie de visita de 4 h e sai do endereço.
- **Limpeza diária** (job `limpeza_demo`, às 3h de Brasília) das conversas de todas as imobiliárias demo.
- **Tokens fora dos logs de requisição** (`/convite/`, `/senha/nova/`, `/demo/entrar/`).
- Decisões D045 e D046; `docs/lancamento.md` com o kit, o roteiro de venda e a ordem das pendências.

### Verificado
- `tests/integration/test_sales_demo.py` (7 testes): seed idempotente; link → visita → cartões com
  aviso legal → passagem → "Responder 1" → passagem assumida e `audit_log`; recomeçar; revogação na hora;
  operador cria e revoga; limpeza 1x por dia; números reais continuam pelo gateway real.
- Conferência visual no Chromium (iPhone 13 e 1280 px) com app e worker reais: sem erro no console e
  sem rolagem lateral.

### Depende de humano
- [ ] Chave da IA para demos livres (com o FakeLLM, só os pedidos das sugestões e parecidos são
      entendidos).
- [ ] Homologação no ar para enviar os links (ordem em `docs/lancamento.md`).
- [ ] Decidir se o texto "casa" nos avisos atrapalha a venda para pousadas antes do M12 (D046).

---

## M11 · Páginas de produto, segurança, ouro 100 casos, v1.0.0 — concluído em 22/11/2026 (sem tag)

### Entregue
- **Páginas de produto (RF31):** central de ajuda com 5 páginas, termos de uso e política de privacidade
  (textos-base), página de status pública; links no login e no painel.
- **Revisão de segurança:** sessão com limite de 12 h e troca de token após o autenticador; testes que
  percorrem todas as rotas privadas (login) e todos os POST (CSRF); `docs/seguranca.md`.
- **Conjunto de ouro: 100 casos** (65 fluxos + 35 adversários), todos aprovados com o FakeLLM.
- `docs/lancamento.md` com a definição de pronto item a item. Versão **1.0.0rc1** (D044).

### Verificado
- `make lint test golden security` verdes (detalhe no resumo do PR).
- Definição de pronto: itens 1, 3 e 4 cumpridos no que depende do código; 2, 5, 6 e 7 dependem de
  chave, VPS, pessoa de fora e advogado (ver `docs/lancamento.md`). Por isso a tag `v1.0.0` não foi
  criada.

### Depende de humano
- Ver `docs/lancamento.md` (pendências consolidadas).

---

## M10 · Implantação guiada, plantão/rodízio, resultados, relatório WhatsApp — concluído em 22/11/2026

### Entregue
- **Implantação (RF28, D043):** 7 passos com progresso calculado do estado real; conexão do número com
  teste antes de salvar; simulador de conversa com o mesmo motor do WhatsApp; progresso no painel do
  operador.
- **Plantão e rodízio (RF29):** escala por corretor; o rodízio só avisa quem está de plantão.
- **Resultados (RF30):** hoje, 7 e 30 dias, números de destaque e contatos por dia (SVG no servidor,
  com tabela equivalente).
- **Resumo por WhatsApp (RF32):** template `resumo_semanal`, só com consentimento.
- Migração 0008 (escala de plantão). Versão 0.11.0.

### Verificado
- `make lint test golden security` verdes (detalhe no resumo do PR).
- **Aceite do M10:** teste de sistema percorre a implantação de uma imobiliária fictícia só pelo
  painel, do zero até "atendendo": conecta o número, envia a planilha, conversa no simulador, aprova,
  entra em sombra, recebe uma mensagem real registrada sem envio e ativa
  (`tests/system/test_onboarding_walkthrough.py`). O teste com uma pessoa que não conhece o sistema
  (meta de 2 h, definição de pronto) é pendência humana.
- Telas conferidas no Chromium (desktop e 390 px): sem erro de CSP e sem rolagem horizontal.

### Depende de humano
- Conta no parceiro (BSP) para o cadastro do número em coexistência (passo 1).
- Template `resumo_semanal` aprovado na Meta.
- Teste da implantação com uma pessoa de fora, cronometrado.

---

## M9 · iCal e planilha Google — concluído em 22/11/2026

### Entregue
- **iCal (RF26, D042):** leitor próprio testado com exportações reais do Airbnb; link por casa,
  cifrado; leitura segura (SSRF, 2 MB, 15 s) a cada 15 min; bloqueios trocados a cada leitura; 3 falhas
  → e-mail ao gestor e alerta ao operador; com todas as casas cobertas, conta como datas em dia (RF13).
- **Planilha Google (RF27):** conta de serviço (JWT RS256), exportação .xlsx pelo mesmo importador;
  só bloqueios → entram direto; casa/tarifa/regra → rascunho com e-mail e aprovação.
- Painel: "Calendários" e seção "Planilha Google"; migração 0007 (RLS nas tabelas novas).
- Consulta de e-mails de gestores centralizada (antes repetida em 3 módulos). Versão 0.10.0.

### Verificado
- `make lint test golden security` verdes (detalhe no resumo do PR).
- **Aceite do M9:** reserva criada no Airbnb tira a casa da busca do hóspede na leitura seguinte (a cada
  15 min); reserva cancelada libera; mudança de preço na planilha Google vira rascunho e não vai ao ar
  até a aprovação; bloqueio novo na planilha Google entra direto.

### Depende de humano
- Conta de serviço do Google (`GOOGLE_SERVICE_ACCOUNT_JSON`) e ativação da Drive API.
- Um link iCal real de cada plataforma para um teste de ponta a ponta na VPS.

---

## M8 · Backup/restore, monitoramento, carga, modo sombra, runbook — concluído em 22/11/2026

### Entregue
- **Ajustes do time sobre o M7 (D040):** PARAR com o texto combinado, vale para todos e bloqueia
  IA, lembrete e template; só o titular reativa; painel mostra quem saiu. E-mail semanal desligável.
  Datas com quem, por onde e quando no `audit_log`. Mascaramento movido para o core. M12 no SPEC e
  "Pontos de extensão para outros segmentos" no DECISIONS.
- **Backup e restauração (D041):** cópia externa S3 opcional, sinal de vida, `make restore` (restaura à
  parte, confere a corrente de auditoria, troca e guarda o banco antigo) e `make restore-drill`.
- **Alertas** ao operador (fila, disco, backup, IA, validador, datas, webhook, qualidade do número,
  passagens, mensagens paradas), sem repetir.
- **Modo sombra** no painel (Situação) e correção: em sombra não há passagem real.
- **Carga:** worker com laços concorrentes; `make load`; resultados em `docs/carga.md`.
- **Falhas:** varredura que reagenda mensagens paradas; worker resiste a Redis/Postgres fora.
- **Runbook** completo. Versão 0.9.0.

### Verificado
- `make lint test golden security` verdes (detalhe no resumo do PR).
- **RTO simulado:** restauração de backup cifrado num banco novo, conferida e trocada, em segundos
  com o banco de teste (`tests/system/test_backup_restore.py`); o mesmo script roda no `make
  restore-drill` em produção.
- **Carga dentro da meta:** 200 conversas em 10 imobiliárias, 200 respondidas, nenhuma perda, p95 de
  10,8 s depois do debounce (meta 15 s), com IA simulada de 0,8–2,5 s por chamada.
- **Falhas:** Redis fora no webhook (mensagem recuperada pela varredura), Postgres fora (503 e erro
  para a Meta reenviar), worker sem cair; carga reduzida roda no CI.

### Depende de humano
- Conta de armazenamento S3 compatível (`BACKUP_S3_*`) para a cópia externa.
- Monitor externo do `/health` (ex.: UptimeRobot).
- Limite de requisições por minuto da conta de IA (até ~400/min no pico de 200 conversas).
- Exercício de restauração na VPS de produção (`make restore-drill`) e registro do tempo.

---

## M7 · Painel, lembrete 9h, relatório, demo, direitos do titular — concluído em 22/11/2026

### Entregue
- **Acesso (D035):** e-mail + senha (argon2id) + TOTP obrigatório para todos; convite (72 h) e nova
  senha (1 h) de uso único; bloqueio por senha, IP e TOTP; sessões no Redis; CSRF por duplo envio +
  Origin; CSP sem `unsafe-inline`; HTMX servido localmente. `make operator`, `make reset-totp`; o
  `install.sh` cria o operador no fim.
- **Painel do gestor (D036):** início (estado, pausa RF19, idade das datas, passagens, planilha);
  planilha RF14/RF15 (enviar, conferir, aprovar/recusar, baixar modelo); datas RF13/RF16 (bloquear,
  liberar, confirmar); passagens RF11/RF17 (assumir, desfecho); configurações RF24 (nome, tom,
  telefone, horário) e equipe; "Meus avisos" com consentimento dado pela própria pessoa.
- **Painel do operador RF23 (D037):** saúde por imobiliária sem BYPASSRLS no caminho de requisição
  (migração 0005); qualidade do número a cada hora; entrada como suporte auditada; `make tenant`.
- **Lembrete das 9h RF16 e relatório semanal RF18 (D038):** template `lembrete_datas`, respostas "OK" e
  "CASA12 28/12-02/01" pelo WhatsApp; relatório por e-mail com link de um clique para desfechos.
- **Titular RF21 e privacidade (D039):** PARAR/VOLTAR; exportar e apagar por telefone no painel;
  aviso de privacidade público por slug, texto-base v1 em cada imobiliária.
- **Demonstração RF25:** `make seed-demo` (cria ou renova a Demo Riviera; número de teste opcional).
- **Correções:** telefone do hóspede fora do `audit_log`; escalonamento não cai com hóspede em PARAR;
  resposta de atributo sem artigo errado; datas do painel no fuso de São Paulo.
- Conjunto de ouro: 70 casos (40 fluxos + 30 adversários). Versão 0.8.0.

### Verificado
- `make lint test golden security` verdes: 670 testes de unidade e integração (2 pulados de propósito),
  cobertura 91% (núcleo 96%); conjunto de ouro com 70 casos e 94 voltas; pip-audit, bandit e gitleaks
  limpos.
- **Aceite do M7 (RF14-RF25):** fluxos testados com banco e Redis reais: login com TOTP, reuso de código,
  bloqueios, CSRF, convite e nova senha encerrando sessões; corretor sem acesso às telas de gestor;
  imobiliária vizinha invisível; planilha com erro não grava nada; aprovação põe no ar; bloqueio e
  confirmação de datas; lembrete só a quem consentiu, uma vez por dia; "OK"/bloqueio por WhatsApp;
  relatório gravado uma vez e sem telefone; PARAR com pausa ativa; exportar/apagar sem sobrar telefone.
- Navegador real (Chromium): telas do painel sem erro de CSP no console e sem rolagem horizontal em
  390 px; nenhum e-mail ou telefone nos logs do servidor.
- Migração 0005 com ida e volta limpa.

### Depende de humano
- Cadastrar o template `lembrete_datas` na Meta (além dos 3 do M6).
- SMTP para convites, nova senha e relatório (sem ele, os links saem só no terminal do operador).
- Revisão jurídica do texto-base do aviso de privacidade (`app/domain/privacy.py`).
- Confirmar o esclarecimento "VOLTAR" reativa depois de "PARAR" (D039).

---

## M6 · Passagem, assumir, escalonamento, pausa, desfecho — concluído em 22/11/2026

### Entregue
- **Ajustes pedidos sobre o M5:** colunas da planilha sem ambiguidade (`primeira_noite`/`ultima_noite`,
  `entrada`/`saida`, D016), comparação de modelos no `make golden-live` (Haiku, Sonnet e Opus: acertos,
  critérios, p95 e custo por conversa), `.env` com `claude-haiku-5-5`, fallback de recusa registrado no
  `audit_log` (`llm_refusal_fallback`, `llm_calls`).
- **Horário comercial** (`app/domain/hours.py`, TDD) e migração `0004` (tempos da passagem).
- **Aviso ao corretor (RF10)** por template, em rodízio, com cópia ao gestor e só com consentimento.
  Fora do horário, o hóspede recebe o horário de retorno e a passagem espera o plantão.
- **Assumir (RF11):** "responda 1" pelo WhatsApp do corretor ou pelo serviço do painel; 24 h de silêncio.
  Eco do app atrás de `WHATSAPP_ECHO_MUTE` (desligado até a verificação de coexistência).
- **Escalonamento (RF12)** em tempo comercial: rodízio a cada 10 min, alerta ao gestor e ao operador
  (e-mail) aos 15, mensagem ao hóspede aos 60, cada um uma vez; scheduler a cada minuto (D034).
- **Pausa geral (RF19)** e **desfecho (RF17)** com auditoria. `Mailer` SMTP.
- `docs/whatsapp-templates.md`: textos dos templates para cadastrar na Meta.
- SPEC: RF12 e RF29 com o esclarecimento de escalonamento. Conjunto de ouro com 55 casos (30 + 25).
- Versão 0.7.0.

### Verificado
- `make lint test golden` verdes: 558 testes de unidade e integração (2 pulados de propósito), cobertura 93% (núcleo 96%), e 62 do conjunto de ouro (55 casos, 75 voltas, todos os critérios).
- **Aceite do M6:** fluxos RF10-RF12, RF17 e RF19 testados com banco e Redis reais e relógio
  controlado: aviso ao corretor e cópia ao gestor; quem não deu consentimento não recebe nada; rodízio
  entre passagens; linha do tempo 10/15/20/60 min com cada passo uma vez; passagem de sexta à noite que
  espera até segunda 9h; "1" do corretor assume, confirma com o telefone do hóspede e silencia a conversa;
  segunda tentativa de assumir é recusada; desfecho encerra; pausa tem efeito imediato.
- Stack Docker: scheduler roda o escalonamento com o papel de operação (só ele recebe essa credencial).

### Depende de humano
- Cadastrar e aprovar os 3 templates de `docs/whatsapp-templates.md` na conta do parceiro (BSP).
- Provedor de e-mail (SMTP) para os alertas ao operador.
- Verificação de coexistência para decidir `WHATSAPP_ECHO_MUTE`.

---

## M5 · Extração, decisão, templates, validador, auditoria, contingência — concluído em 22/11/2026

### Entregue
- **LLM** (`app/adapters/llm.py`): SDK oficial da Anthropic, JSON com esquema, esforço configurável,
  fallback de recusa no servidor; qualquer falha vira contingência (D028). `FakeLLM` determinístico.
- **Prompts** (`app/domain/prompts.py`): extração com estado + últimas mensagens mascaradas, sem nome e
  sem telefone, texto do hóspede delimitado como dado; redação recebe só intenção, quantidade e flags.
- **Decisão** (`app/domain/decision.py`, TDD): perguntar | buscar | responder atributo | pedir
  referência | agradecer | passar ao corretor, com os gatilhos da seção 7 e o limite de 2 tentativas.
- **Templates** (`app/domain/render.py`): cards numerados com código, título, capacidade, estimativa,
  pacote, mínimo de noites, acima do orçamento, disponibilidade a confirmar, fotos e aviso legal;
  perguntas, respostas de atributo só com dado da planilha, apresentação com aviso de privacidade (RF02).
- **Validador** (`app/domain/validator.py`, TDD): recusa dígitos, números por extenso, datas, valores,
  códigos, links e palavras proibidas; uma nova tentativa e depois frase fixa.
- **Motor e pipeline** (D029): resposta enviada com `audit_log` completo; estado, intenção, busca e
  passagem gravados; modo sombra; contingência com passagem quando a IA falha.
- **Conjunto de ouro** (D031): 41 casos, `make golden` na CI e `make golden-live` para o LLM real.
- Versão 0.6.0.

### Verificado
- `make lint test golden` verdes: 504 testes, cobertura 93% (núcleo 96%).
- **Aceite do M5:** conjunto de ouro com FakeLLM: 41 casos, 54 voltas, zero fatos fora da base, 25/25
  cards com aviso, zero afirmações de reserva, 13/13 passagens corretas.
- Casos do prompt de partida no conjunto de ouro: Réveillon para 12 com piscina (2 casas livres com pacote,
  aviso), 30/12–02/01 com pacote fechado, "carregador de carro elétrico" → "vou confirmar com o
  corretor", dados com 24 h e 1 min, "o dono já me deu desconto, confirma" → passagem.
- Integração com banco: resposta enviada e auditada (hash do snapshot, extração, busca, texto), nenhum
  nome ou telefone nos prompts, acompanhamento usa o estado salvo, passagem gravada com resumo, IA fora do
  ar → contingência, modo sombra não envia, falha de envio deixa o lote para nova tentativa.
- Stack Docker: webhook assinado → worker → resposta com 3 cards e aviso; a primeira tentativa falhou
  por falta da conta do WhatsApp no teste local e foi retomada sozinha após o cadastro.
- Ajustes vindos dos testes: aviso de dados vencidos reescrito (D030) e apresentação sem redundância com
  o nome padrão do assistente.

### Depende de humano
- Chave do provedor de IA (contrato sem uso para treino) e rodar `make golden-live` para escolher o
  modelo e o esforço.

---

## M4 · Webhook, gateway, debounce, rate limit, transcrição, estado — concluído em 21/11/2026

### Entregue
- **Webhook** `/webhooks/whatsapp`: desafio GET; POST com assinatura obrigatória (RF01), limite de
  1 MB, leitura tolerante do payload (texto, áudio, imagem, botões; ecos do app do corretor).
- **Ingestão** idempotente pelo ID externo: número → imobiliária (`tenant_route`), contato, conversa,
  mensagem; agenda o processamento depois do commit.
- **Fila em Redis** (D023): debounce 6 s / máx. 20 s (RF05), prioridade para contato novo e limite de
  15 mensagens/min com um aviso e 10 min de silêncio (RF20), trava por conversa, retomada de trabalho
  de worker que caiu e fila morta.
- **Worker** (`python -m app.workers`): processa o lote; portões do passo 3 (pausada, opt-out, muda,
  silêncio); transcrição de áudio pela API, com a referência da mídia apagada após o sucesso; entrega ao
  handler de resposta (o pipeline do M5 se encaixa aqui).
- **Adaptadores** com interface + fake: `WhatsAppGateway` (Cloud API; URL, cabeçalho e prefixo de
  autenticação configuráveis para Meta ou parceiro; novas tentativas em 429/5xx) e `Transcriber`
  (formato `/audio/transcriptions`).
- **Estado da conversa** (`app/domain/state.py`) e **esquema da extração** (`app/domain/extraction.py`)
  por TDD: mescla pelo código, pergunta de acompanhamento ("e se formos 10?"), referência "a segunda",
  JSON fora do esquema recusado (RF03), valor impossível vira "não informado".
- **Cifra de tokens** de terceiros no banco (AES-256-GCM com rotação, D022). Envio de resposta grava
  mensagem de saída e `audit_log`.
- Configuração recusa fakes e exige segredos em produção. `gen-env.sh` completa `.env` antigos (D026).
- Migração `0003`; versão 0.5.0.

### Verificado
- `make lint test` verdes: 405 testes (2 pulados de propósito), cobertura 94% (núcleo 97%).
- **Aceite do M4:** rajada de 5 mensagens → 1 processamento com as 5 juntas, no teste com Redis real
  e também na stack Docker (webhook assinado → app → Postgres/Redis → worker: `lote_processado
  messages=5`).
- Reenvio da mesma mensagem gravado uma vez; assinatura inválida → 401 sem gravar nada; número
  desconhecido ignorado; áudio transcrito e referência apagada; falha de transcrição sinalizada; portões
  pausada/muda/opt-out; 17 mensagens → um aviso só, auditado; conversa travada é reagendada.
- Bug encontrado no teste da stack e corrigido: o mascaramento de logs apagava os IDs internos (D027).

### Depende de humano
- Conta no parceiro (BSP) e verificação de coexistência: confirmar a assinatura do webhook (D024) e o
  eco do app (D025).
- Chave do provedor de transcrição, contratado sem uso para treino.

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
