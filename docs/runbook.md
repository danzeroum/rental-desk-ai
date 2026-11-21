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

URL do webhook para cadastrar: `https://<domínio>/webhooks/whatsapp`.
O token de acesso de cada imobiliária fica cifrado no banco (assistente de implantação, M10).

## Comandos do dia a dia

| Comando | O que faz |
|---|---|
| `make ps` | Estado dos serviços |
| `make logs` | Logs ao vivo (JSON, sem dados pessoais) |
| `make up` | Sobe ou atualiza tudo |
| `make down` | Para tudo (dados preservados nos volumes) |
| `make migrate` | Aplica migrações |
| `make backup` | Backup cifrado imediato (fica no volume `backups`) |

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
