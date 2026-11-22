# Runbook de operação

Escrito para que outra pessoa opere o sistema sem o autor. Cresce a cada marco; a versão
completa (incidentes, restauração, alertas) fecha no M8.

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
| `make seed-demo` | Cria ou renova a "Imobiliária Demo Riviera" (10 casas fictícias, temporada corrente). |

O gestor convida a equipe em **Configurações**; cada pessoa aceita os avisos no WhatsApp na própria página
**Meus avisos** (sem aceite, nenhuma mensagem ativa sai). O operador vê a saúde de todas as imobiliárias
em `/operador` e entra em qualquer uma com "Entrar como suporte" (fica no `audit_log`).

**Demonstração com o número de teste da Meta:** exporte o token só no shell e passe o `phone_number_id`:
```bash
export DEMO_WHATSAPP_TOKEN='...'   # não vai para o .env nem para o histórico do git
make seed-demo PNID=123456789012345
```

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

## Fila

- Trabalhos que falharam 3 vezes vão para o stream `rd:jobs:dead` no Redis
  (`docker compose ... exec redis redis-cli XRANGE rd:jobs:dead - +`).
- Logs úteis: `webhook_processado`, `lote_processado`, `lote_sem_resposta`, `limite_anti_abuso_atingido`,
  `transcricao_falhou`, `trabalho_falhou`.

## Saúde

- `https://<domínio>/health` → `{"status":"ok", "checks": {"postgres":"ok","redis":"ok"}}`.
  Status 503 indica qual dependência falhou; o motivo está em `make logs` (evento `health_check_falhou`).
- `worker` e `scheduler` gravam heartbeat no Redis a cada 10 s; o Docker marca o contêiner como
  `unhealthy` se o heartbeat sumir.

## Atualizar e voltar versão

1. `git fetch --tags && git checkout v<nova versão>`, ajuste `APP_VERSION=<nova versão>` no `.env`,
   depois `make migrate` e `make up`.
2. Rollback: `git checkout v<versão anterior>`, volte `APP_VERSION` e rode `make up`. A imagem da
   versão anterior continua no Docker com a sua tag. As migrações são sempre compatíveis com a versão
   anterior (SPEC, seção 10), então não é preciso desfazê-las.

## Congelamento

De 20/12 a 02/01 e no Carnaval: só correções urgentes.
