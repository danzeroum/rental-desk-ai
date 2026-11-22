# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versionamento semântico (veja `DECISIONS.md`, D007).

## [Não lançado]

## [0.8.0] - 2026-11-22 · M7 Painel, lembrete, relatório, demonstração e titular

### Adicionado
- Painel com e-mail + senha + TOTP obrigatório para todos; convite, nova senha, bloqueio por tentativas,
  CSRF e CSP restrita.
- Painel do gestor: estado e pausa do assistente, envio/conferência/aprovação da planilha, datas (bloquear,
  liberar, confirmar), passagens (assumir, desfecho), configurações (nome, tom, telefone, horário),
  equipe e "Meus avisos" (consentimento dado pela própria pessoa).
- Painel do operador: saúde de cada imobiliária e entrada como suporte auditada; `make tenant`,
  `make operator`, `make reset-totp`.
- Lembrete das 9h por WhatsApp com respostas "OK" e "CASA12 28/12-02/01"; relatório semanal por e-mail.
- Direitos do titular: PARAR/VOLTAR, exportar e apagar por telefone; aviso de privacidade público.
- Modo demonstração (`make seed-demo`), com o número de teste da Meta opcional.
- Qualidade do número consultada a cada hora. Conjunto de ouro com 70 casos.

### Corrigido
- Telefone do hóspede não vai mais para o `audit_log` na confirmação ao corretor.
- Falha no escalonamento quando o hóspede tinha pedido PARAR.
- Resposta de atributo com artigo errado em casas de nome masculino ("A Sobrado").

## [0.7.0] - 2026-11-22 · M6 Passagem ao corretor

### Adicionado
- Aviso de passagem ao corretor de plantão por template do WhatsApp, em rodízio, com cópia ao gestor e
  consentimento obrigatório.
- Assumir pelo "responda 1" ou pelo painel, com 24 h de silêncio do assistente; eco do app opcional.
- Escalonamento em tempo comercial (10/15/60 min) com scheduler; resposta fora do horário com o horário de
  retorno.
- Pausa geral e desfecho das passagens, auditados; envio de e-mail por SMTP.
- Comparação de modelos no `make golden-live` e registro do fallback de recusa no `audit_log`.
- Conjunto de ouro com 55 casos.

### Alterado
- Planilha: colunas `primeira_noite`/`ultima_noite` (Tarifas) e `entrada`/`saida` (Bloqueios).
- Modelo sugerido no `.env`: `claude-haiku-5-5`.

## [0.6.0] - 2026-11-22 · M5 Resposta ao hóspede

### Adicionado
- Extração de intenção por LLM com JSON validado contra esquema; FakeLLM determinístico.
- Decisão do próximo passo com gatilhos de passagem ao corretor.
- Cards e textos por template, com aviso legal; respostas de atributo só com dado da planilha.
- Validador das frases do LLM, com nova tentativa e frase fixa.
- Pipeline de resposta com trilha de auditoria completa, modo sombra e contingência.
- Conjunto de ouro com 41 casos (`make golden`, `make golden-live`).

### Corrigido
- Mensagens do lote só são marcadas como tratadas depois do envio da resposta.

## [0.5.0] - 2026-11-21 · M4 Webhook, fila e transcrição

### Adicionado
- Webhook oficial do WhatsApp com assinatura obrigatória e gravação idempotente.
- Fila em Redis com debounce (6 s / 20 s), prioridade para contato novo, limite anti-abuso e
  retomada de trabalho.
- Worker com portões de atendimento (pausa, opt-out, conversa assumida, silêncio) e transcrição de
  áudio por API.
- Gateway da Cloud API (Meta ou parceiro) e transcritor, com fakes.
- Estado da conversa e esquema da extração validados.
- Cifra AES-256-GCM dos tokens de terceiros no banco.

### Corrigido
- Logs não mascaram mais os IDs internos (`tenant_id`, `conversation_id`).

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
