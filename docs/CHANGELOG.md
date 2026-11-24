# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versionamento semântico (veja `DECISIONS.md`, D007).

## [Não lançado]

### Segurança
- Revisão LGPD de 24/11/2026 (`docs/revisao-2026-11-24.md`), itens P1:
  - Risco-002: o filtro por telefone e a exportação do Rastreio passam a ser POST com CSRF; o
    telefone não vai mais na URL. Os logs do Caddy (acesso e erro) apagam `telefone`/`phone` da URL
    e encurtam o IP.

### Alterado
- API da Meta na versão v25.0 por padrão (era v21.0). Em instalações existentes, ajuste
  `WHATSAPP_API_BASE_URL` no `.env`.
- Rastreio: consultas, exportações e acessos de suporte saem da visão "todos" e ganham o filtro
  "acessos"; continuam gravados na trilha e no CSV de "todos".

### Adicionado
- Rastreio de ponta a ponta (D047):
  - status de entrega da Meta (enviada, entregue, lida, falhou com código) gravado na mensagem e
    na trilha imutável;
  - cada mensagem recebida e cada eco do app WhatsApp Business também entram na trilha;
  - entrega que falhou vira erro na Saúde do operador;
  - página `/operador/rastreio` com filtros, conferência da cadeia de hash, linha do tempo da
    conversa e exportação em CSV; as consultas ficam registradas.
- `scripts/numero-teste-meta.sh` e `docs/numero-teste-meta.md`: liga o número de teste da Meta à Demo
  Riviera em um comando (credenciais sem eco, provedor oficial, conferência do webhook) e explica o
  modo sombra enquanto a Meta bloquear envios (erro 130497).
- `scripts/teste-local.sh` e `docs/teste-local.md`: instalação local em um comando (portas
  automáticas, operador, demonstração, link do kit, testes opcionais) e roteiro de teste de cada tela.
- `make demo-link` / `python -m app.cli create-demo-link`: link do kit pela linha de comando.
- Portas configuráveis (`HTTP_PORT`, `HTTPS_PORT`, `PUBLIC_BASE_URL`) para teste local.
- `AGENTS.md` para agentes de código além do Claude Code.
- Kit de demonstração para vendas: conversa com aparência de WhatsApp em `/demo` pelo pipeline real,
  celular do corretor com "Responder 1", imobiliárias fictícias Maré Alta (8 casas) e Pousada Recanto da
  Enseada (6 quartos), links com token revogáveis em `/operador/demo` e limpeza diária.

### Corrigido
- Rastreio: identificadores internos (ids de usuário, planilha, mensagem) saíam como `[CHAVE_PIX]`
  no CSV e na tela, porque têm o formato de uma chave Pix aleatória; a máscara agora vale só para
  texto livre (`mask_structured`). Coluna `data_hora_utc` renomeada para `data_hora` (vem com o fuso).
  Todos os eventos da trilha têm descrição em português.
- Erro de envio pelo WhatsApp agora registra o código e o motivo que a Meta devolve (mascarados);
  antes o log dizia só "API respondeu 400", sem dizer se era token, versão ou destinatário.
- `numero-teste-meta.sh` parava sem aviso na conferência do webhook quando o `.env` não tinha
  `PUBLIC_BASE_URL` (o padrão na VPS); agora usa `https://<DOMAIN>`. Também recusa uma chave secreta
  fora do formato (32 hex) ou um token que não começa com EAA, e recria os serviços para aplicar a
  chave nova.
- QR do autenticador (TOTP) saía cortado na tela e não era lido pelo app; agora escala sem
  cortar, com margem e fundo branco também no modo escuro.
- Painel do operador mostrava a situação em inglês ("active"); agora "atendendo", "modo sombra" etc.
- Imobiliárias de demonstração não geram mais o alerta "nenhuma mensagem recebida em 48 h".
- Demonstração: as datas das empresas fictícias são renovadas na limpeza diária; antes, depois de 24 h,
  os cartões diziam que a disponibilidade não foi atualizada.
- Demonstração no computador: sugestões em linhas, sem barra de rolagem horizontal.
- Guia de teste local citava o webhook em `/webhook`; o caminho é `/webhooks/whatsapp`.

### Segurança
- Tokens de convite, troca de senha e demonstração não aparecem mais no log de requisições.

## [1.0.0rc1] - 2026-11-22 · M11 Candidata à Versão 1.0

### Adicionado
- Central de ajuda (`/ajuda`), termos de uso, política de privacidade e página de status pública.
- Conjunto de ouro com 100 casos (65 fluxos e 35 adversários).
- Revisão de segurança (`docs/seguranca.md`) com testes que percorrem todas as rotas.
- Checklist do lançamento (`docs/lancamento.md`).

### Alterado
- Sessão do painel com limite de 12 h e token trocado ao concluir o autenticador.

### Corrigido
- Worker marcado como "unhealthy" no CI: o teste de saúde importava o processo inteiro (2,3 s) e,
  com os 40 laços ativos, estourava o limite de 5 s. Agora é `python -m app.core.healthcheck`.

## [0.11.0] - 2026-11-22 · M10 Implantação guiada, plantão, resultados

### Adicionado
- Assistente de implantação com 7 passos e progresso salvo; conexão do número com teste antes de
  salvar; simulador de conversa no painel; progresso visível ao operador.
- Escala de plantão por corretor; o rodízio só avisa quem está de plantão.
- Painel de resultados (hoje, 7 e 30 dias) com contatos por dia.
- Resumo semanal por WhatsApp (template `resumo_semanal`), com consentimento.

### Corrigido
- O app não subia com `GOOGLE_SERVICE_ACCOUNT_JSON` vazio (padrão do compose); vazio ou inválido agora
  só desliga a ligação com o Google, com erro no log.

## [0.10.0] - 2026-11-22 · M9 Calendários e planilha Google

### Adicionado
- Calendário iCal por casa (Airbnb, Booking, Vrbo ou sistema próprio): leitura a cada 15 min, bloqueios
  automáticos, alerta após 3 falhas, datas em dia sozinhas quando todas as casas têm calendário.
- Planilha Google sincronizada: bloqueios entram direto; mudança de casa, tarifa ou regra vira versão que
  pede aprovação, com e-mail ao gestor.
- Telas "Calendários" e "Planilha Google" no painel; central de ajuda `app/web/content/ajuda/calendarios.md`.

### Corrigido
- Tela do modo sombra mostrava a mensagem anterior do hóspede (a resposta agora guarda os ids das
  mensagens do lote, sem o texto).

## [0.9.0] - 2026-11-22 · M8 Operação

### Adicionado
- Cópia externa do backup (S3 compatível, opcional), `make restore` (restaura à parte, confere e troca)
  e `make restore-drill` (exercício com tempo medido).
- Alertas ao operador por e-mail: fila, disco, backup, IA fora, validador, datas, webhook, qualidade do
  número, passagens e mensagens paradas.
- Tela "Situação" no painel: modo sombra para conferir respostas e ativar o atendimento.
- Worker com várias conversas ao mesmo tempo (`WORKER_CONCURRENCY`); teste de carga (`make load`).
- Recuperação automática de mensagens paradas; worker resiste a Redis e Postgres fora do ar.
- Runbook completo (alertas, backup, falhas, incidentes).
- Ajustes do time: PARAR vale para todos e bloqueia templates; e-mail semanal desligável; auditoria
  das datas com quem, por onde e quando.

### Corrigido
- Modo sombra criava passagem real e avisava o corretor.
- Mensagem perdida quando o Redis caía entre gravar e agendar.
- Fila de falhas sem limite; restauração sem os donos das tabelas.

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
