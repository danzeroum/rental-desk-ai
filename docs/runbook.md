# Runbook de operação

Escrito para que outra pessoa opere o sistema sem o autor: instalação, rotina, alertas,
backup e restauração, falhas e incidentes.

## Instalar numa VPS nova

1. VPS Ubuntu LTS no Brasil (sugestão inicial: 4 vCPU, 8 GB), domínio com registro A apontando para ela.
2. `git clone` do repositório na VPS e, na raiz dele:
   ```bash
   sudo DOMAIN=app.suaempresa.com.br ./deploy/install.sh
   ```
   Homologação, em outra VPS: `sudo ENV=staging DOMAIN=homolog.suaempresa.com.br ./deploy/install.sh`.
3. O script instala o Docker, gera `.env` (permissão 600) com segredos aleatórios, sobe os serviços,
   aplica as migrações e confere o `/health`.
4. **Guarde uma cópia do `.env` fora da VPS** (cofre de senhas). Sem o `BACKUP_PASSPHRASE` os backups
   não podem ser abertos.

O `install.sh` pode ser rodado de novo: não troca o `.env` nem apaga dados. Para mudar o domínio,
edite `DOMAIN` no `.env` e rode `make up`.

## Conectar o WhatsApp e a transcrição

No `.env` (depois `make up`):

| Variável | Valor |
|---|---|
| `WHATSAPP_PROVIDER` | `cloud_api` |
| `WHATSAPP_API_BASE_URL` | URL da Cloud API da Meta ou do parceiro |
| `WHATSAPP_AUTH_HEADER` / `WHATSAPP_AUTH_SCHEME` | ex.: `Authorization` + `Bearer` (Meta); `D360-API-KEY` + vazio (parceiro) |
| `WHATSAPP_APP_SECRET` | segredo do app, para conferir a assinatura do webhook |
| `WHATSAPP_VERIFY_TOKEN` | gerado pelo `gen-env.sh`; cadastrar o mesmo no parceiro/Meta |
| `TRANSCRIBER_PROVIDER` / `TRANSCRIBER_API_KEY` | `openai_compatible` + chave (contrato sem uso para treino) |
| `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` | `anthropic` + modelo escolhido pelo `make golden-live` + chave |
| `LLM_EFFORT` | `low` (padrão); subir só se o conjunto de ouro mostrar ganho |

URL do webhook para cadastrar: `https://<domínio>/webhooks/whatsapp`.
O token de acesso de cada imobiliária fica cifrado no banco (assistente de implantação, M10).

## Primeiro acesso, imobiliárias e equipe

O painel exige e-mail + senha + autenticador (TOTP) para todos.

| Comando | O que faz |
|---|---|
| `make operator EMAIL=voce@exemplo.com` | Cria o operador e imprime o convite (72 h; também por e-mail com SMTP). O `install.sh` já pergunta no fim. |
| `make tenant NAME="Imobiliária Sol" EMAIL=gestor@sol.com` | Cria a imobiliária (em implantação), o aviso de privacidade v1 e convida o gestor. Roda no contêiner do scheduler, o único com o papel de operação. |
| `make reset-totp EMAIL=pessoa@exemplo.com` | Perdeu o celular: apaga o autenticador e encerra as sessões; no próximo login a pessoa cadastra outro. |
| `make seed-demo` | Cria ou renova a "Imobiliária Demo Riviera" (10 casas fictícias, temporada corrente) e as do kit de vendas (Maré Alta e Pousada Recanto da Enseada). Links do kit: Operador → Demonstração (`/operador/demo`); revogar desliga na hora. As conversas das imobiliárias demo são apagadas às 3h. |

O gestor convida a equipe em **Configurações**; cada pessoa aceita os avisos no WhatsApp na própria página
**Meus avisos** (sem aceite, nenhuma mensagem ativa sai). O operador vê a saúde de todas as imobiliárias
em `/operador` e entra em qualquer uma com "Entrar como suporte" (fica no `audit_log`).

**Demonstração com o número de teste da Meta:** exporte o token só no shell e passe o `phone_number_id`:
```bash
export DEMO_WHATSAPP_TOKEN='...'   # não vai para o .env nem para o histórico do git
make seed-demo PNID=123456789012345
```

## Calendários e planilha Google

- O scheduler lê os calendários iCal e as planilhas Google a cada 15 min (`job=calendarios`,
  `job=planilha_google`). Logs: `calendario_sincronizado`, `calendario_falhou`,
  `planilha_google_sincronizada`, `planilha_google_falhou`.
- **Conta de serviço do Google (uma vez):** no Google Cloud, crie um projeto, ative a **Google Drive
  API**, crie uma conta de serviço, gere uma chave JSON e cole o JSON inteiro em uma linha em
  `GOOGLE_SERVICE_ACCOUNT_JSON` no `.env` (depois `make up`). O e-mail da conta aparece no painel para
  os gestores compartilharem a planilha.
- Ajuda para o gestor: `app/web/content/ajuda/calendarios.md`.

## Lembrete das 9h e relatório semanal

- O scheduler manda o template `lembrete_datas` uma vez por dia (9h-18h) ao gestor que aceitou em "Meus
  avisos". Respostas: `OK` confirma as datas; `CASA12 28/12-02/01` bloqueia (a noite da saída fica livre).
- Toda segunda (8h-18h) gera o relatório da semana anterior, guarda em `weekly_report` e envia por e-mail
  aos gestores. Logs: `tarefa_agendada_concluida job=lembrete_9h` / `job=relatorio_semanal`.
- A qualidade do número na Meta é consultada a cada hora (`job=qualidade_numero`) e aparece em `/operador`.

## Direitos do titular (LGPD)

- Hóspede que escreve **PARAR** recebe uma confirmação e deixa de receber qualquer mensagem automática
  (inclusive o aviso de espera da passagem). **VOLTAR** reativa.
- Pedido de acesso ou exclusão: gestor em **Painel → Titulares**, pelo telefone: exportar (JSON) ou
  apagar (digitando APAGAR). Apagar remove conversas, mensagens, transcrições e passagens, e tira o
  telefone das confirmações enviadas a corretores. O `audit_log` fica, sem telefone nem texto do hóspede.
- Aviso de privacidade público: `https://<domínio>/privacidade/<slug>` (a versão vai na 1ª resposta).
  **Texto-base em `app/domain/privacy.py` precisa de revisão jurídica** antes do lançamento.

## Rastreio: comprovar a um cliente o que aconteceu

Operador → **Rastreio** (`/operador/rastreio`):

1. **Escolha a imobiliária e o período.** Para uma conversa específica, digite o telefone do
   hóspede, com ou sem o 55 e com ou sem o 9. O telefone vai no corpo do formulário, nunca na
   URL: um link com `?telefone=` é ignorado (a URL fica no log de acesso). O Caddy também apaga
   `telefone`/`phone` da URL e encurta o IP nos próprios logs.
2. **Confira o selo "cadeia íntegra".** O banco recalcula o hash de todos os eventos daquela
   imobiliária na hora. Se aparecer **"cadeia QUEBRADA"**, trate como incidente de segurança
   (seção abaixo): não apague nada.
3. **Abra "ver conversa".** Aparece cada mensagem com hora, quem enviou e o status da Meta
   (enviada, entregue, lida ou **NÃO ENTREGUE** com o código). Os códigos mais comuns:

   | Código | O que é |
   |---|---|
   | 130497 | Conta bloqueada para enviar ao país do destinatário. Peça a verificação da empresa e abra chamado na Meta. |
   | 131047 | Passou a janela de 24 h: só por template aprovado. |
   | 131026 | Destinatário não alcançável (sem WhatsApp ou bloqueou o número). |
   | 190 | Token inválido ou vencido: gere outro e rode `scripts/numero-teste-meta.sh` ou reconecte em Configurações. |

4. **"Exportar CSV"** gera o arquivo com o hash de cada evento. Ele serve de prova: qualquer
   alteração no banco quebraria a cadeia.

Toda consulta e exportação fica registrada na própria trilha, com quem consultou e quando.

## Comandos do dia a dia

| Comando | O que faz |
|---|---|
| `make ps` | Estado dos serviços |
| `make logs` | Logs ao vivo (JSON, sem dados pessoais) |
| `make up` | Sobe ou atualiza tudo |
| `make down` | Para tudo (dados preservados nos volumes) |
| `make migrate` | Aplica migrações |
| `make backup` | Backup cifrado imediato (fica no volume `backups`) |

## Passagens ao corretor

- Templates obrigatórios na conta do WhatsApp: `docs/whatsapp-templates.md`.
- Corretor e gestor só recebem aviso com WhatsApp cadastrado e consentimento registrado.
- O `scheduler` escalona a cada minuto (log `tarefa_agendada_concluida job=escalonamento`); se ele parar,
  passagens não giram nem alertam. Ele precisa de `DATABASE_OPS_URL` (montada pelo compose).
- Logs úteis: `passagem_assumida`, `passagem_sem_corretor_de_plantao`, `aviso_passagem_falhou`,
  `alerta_operador_falhou`, `passagem_na_fila_fora_do_horario`.

## Situação da imobiliária e modo sombra

Implantação → **modo sombra** → atendendo, em **Painel → Situação** (gestor ou operador como suporte).
No modo sombra o assistente lê as conversas reais e só registra o que responderia; a equipe segue
atendendo pelo app e nenhuma passagem real é criada. Confira as respostas por 2 dias: preço, data ou
casa errada é problema na planilha ou no calendário. Ativar antes de 2 dias exige confirmação. Cada
mudança fica no `audit_log` (`status_changed`).

## Fila

- Trabalhos que falharam 3 vezes vão para o stream `rd:jobs:dead` no Redis
  (`docker compose ... exec redis redis-cli XRANGE rd:jobs:dead - +`).
- Logs úteis: `webhook_processado`, `lote_processado`, `lote_sem_resposta`, `limite_anti_abuso_atingido`,
  `transcricao_falhou`, `trabalho_falhou`.

## Capacidade

- O worker atende `WORKER_CONCURRENCY` conversas ao mesmo tempo (padrão 40), porque quase todo o
  tempo é espera pela IA. Medido em `docs/carga.md`: 200 conversas em 10 imobiliárias com p95 de
  10,8 s com IA rápida. Com modelo mais lento, use 64. Para dobrar, suba outra réplica do worker.
- O Postgres sobe com `max_connections=200`.

## Saúde

- `https://<domínio>/health` → `{"status":"ok", "checks": {"postgres":"ok","redis":"ok"}}`.
  Status 503 indica qual dependência falhou; o motivo está em `make logs` (evento `health_check_falhou`).
- `worker` e `scheduler` gravam heartbeat no Redis a cada 10 s; o Docker marca o contêiner como
  `unhealthy` se o heartbeat sumir.

## Alertas

O scheduler confere tudo a cada 5 min e manda **um e-mail aos operadores** com os alertas novos (o
mesmo alerta não se repete por 12 h). Também aparecem em `/operador` e no log `alerta_operacional`.
Configure ainda um **monitor externo** (ex.: UptimeRobot) chamando `https://<domínio>/health` a cada
minuto: ele avisa se a VPS inteira cair, coisa que o próprio sistema não consegue avisar.

| Alerta | O que fazer |
|---|---|
| Tarefas acumuladas na fila (≥ 100; erro com ≥ 500) | `make ps`: o worker está `healthy`? `make logs` (`trabalho_falhou`, `worker_dependencia_indisponivel`). Limite da conta de IA? Se for volume, suba `WORKER_CONCURRENCY` ou outra réplica. |
| Tarefa(s) na fila de falhas | Ver o motivo nos logs (`trabalho_falhou` com `conversation_id`); a conversa precisa de um corretor. |
| Mensagem sem processar há mais de 10 min | O scheduler reagenda sozinho a cada minuto (`mensagens_paradas_reagendadas`); se persistir, o worker está parado. |
| Disco ≥ 80% (erro com ≥ 90%) | `docker system prune` (imagens antigas), `du -sh` no volume de backups; aumente o disco. |
| Nenhum backup nas últimas 26 h | `make logs` do serviço `backup` (`backup_falhou`); rode `make backup`. |
| Backup sem cópia externa | Configure `BACKUP_S3_*` no `.env` e rode `make up` (ou veja `backup_envio_externo_falhou`). |
| Respostas em contingência (IA fora) | Status do provedor de IA; chave e saldo; o hóspede recebe resposta fixa e passagem. |
| Frases recusadas pelo validador | Normal em pequeno número; muitas seguidas indicam mudança de modelo ou de prompt: rode `make golden-live`. |
| Sem planilha aprovada / datas vencidas | Avise o gestor (painel ou lembrete das 9h). |
| Passagem aguardando há mais de 15 min | O gestor já recebeu o alerta; confirme que há corretor com aviso aceito. |
| Nenhuma mensagem em 48 h | Webhook desligado no parceiro? Número desconectado? Teste mandando uma mensagem. |
| Qualidade do número amarela/vermelha | Muitos bloqueios ou denúncias de hóspedes; revise os templates e a frequência de mensagens ativas. |

## Backup e restauração

- Todo dia às `BACKUP_HOUR` (03h) o serviço `backup` gera um dump **cifrado** (gpg AES-256 com
  `BACKUP_PASSPHRASE`), guarda 30 dias no volume `backups` e, com `BACKUP_S3_*` preenchido, envia uma
  cópia para o armazenamento externo (S3 compatível), que também guarda 30 dias. `make backup` faz um
  agora.
- **Exercício de restauração (trimestral, e antes de cada temporada):** `make restore-drill` restaura o
  último backup num banco à parte, confere as tabelas e a corrente do `audit_log` de cada imobiliária,
  mostra o tempo e apaga. Anote o tempo no registro de exercícios. `make restore-drill FILE=remote`
  testa a cópia externa.
- **Restaurar de verdade** (RTO ≤ 4 h, RPO ≤ 24 h):
  1. `make restore FILE=latest CONFIRM=sim` (ou `FILE=remote`, ou o caminho de um arquivo em
     `/backups`). Isso para app, worker e scheduler, restaura num banco à parte, confere, troca pelo
     banco em uso (o antigo fica como `rentaldesk_antes_<data>`), aplica migrações e sobe tudo.
  2. Confira `/health`, entre no painel e veja a última conversa.
  3. Mensagens recebidas entre o backup e a falha: a Meta reenvia o que não teve resposta 200; o
     resto se perde (RPO). Avise as imobiliárias.
  4. Depois de alguns dias, apague o banco antigo:
     `docker compose ... exec postgres dropdb -U postgres rentaldesk_antes_<data>`.
- **VPS perdida:** instale uma nova com o `install.sh`, copie o `.env` guardado no cofre (sem o
  `BACKUP_PASSPHRASE` original os backups não abrem), rode `make restore FILE=remote CONFIRM=sim` e
  aponte o domínio para a nova VPS.

## Falhas

| Falha | O que acontece | O que fazer |
|---|---|---|
| IA fora do ar | Respostas fixas (contingência) e passagem ao corretor; alerta "contingência". | Esperar o provedor; trocar `LLM_MODEL` se for um modelo só. |
| Redis fora | `/health` 503; o webhook grava a mensagem e responde erro, a Meta reenvia; o worker espera e tenta de novo sem cair. Quando o Redis volta, o scheduler reagenda as mensagens paradas em até 1 min. | `make ps`, `make logs redis`; `make up`. |
| Postgres fora | `/health` 503; webhook responde erro e a Meta reenvia (por até alguns dias). | `make logs postgres`; disco cheio? `make up`. Se corrompido: restauração. |
| WhatsApp (parceiro/Meta) fora | Respostas falham com `whatsapp_api_erro`; tentativas automáticas; depois vão para a fila de falhas. | Status do parceiro; após a volta, as conversas com falha precisam de corretor. |
| VPS inteira fora | O monitor externo avisa. | Provedor da VPS; se perdida, "VPS perdida" acima. |

## Incidente de segurança ou de dados pessoais

1. **Detectar e conter:** pause o assistente das imobiliárias afetadas (painel) ou `make down`; troque
   as credenciais expostas (`.env`, tokens do parceiro, chave da IA) e encerre sessões do painel
   (`make reset-totp` para quem for preciso).
2. **Avaliar:** o que vazou, de quem, desde quando (`audit_log`, logs de 30 dias).
3. **Avisar a imobiliária (controladora) em até 24 h**, por escrito, com o que se sabe; ela decide sobre
   avisar a ANPD e os titulares.
4. **Registrar** em `docs/incidentes/AAAA-MM-DD.md`: linha do tempo, causa, dados afetados, ações.

## Atualizar e voltar versão

1. `git fetch --tags && git checkout v<nova versão>`, ajuste `APP_VERSION=<nova versão>` no `.env`,
   depois `make migrate` e `make up`.
2. Rollback: `git checkout v<versão anterior>`, volte `APP_VERSION` e rode `make up`. A imagem da
   versão anterior continua no Docker com a sua tag. As migrações são sempre compatíveis com a versão
   anterior (SPEC, seção 10), então não é preciso desfazê-las.

## Congelamento

De 20/12 a 02/01 e no Carnaval: só correções urgentes.
