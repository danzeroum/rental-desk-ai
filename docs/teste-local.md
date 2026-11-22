# Teste local: subir o Rental Desk AI no seu computador e conhecer tudo

Este guia leva de "nada instalado" até o produto rodando no seu computador, com dados fictícios,
e depois percorre cada parte do que foi construído (M0 a M11 e o kit de demonstração), dizendo
onde ver, o que testar e onde está no código.

Nada aqui fala com o WhatsApp de verdade nem envia e-mail: o WhatsApp, o e-mail e a transcrição
usam implementações falsas (`fake`). A IA também é falsa (FakeLLM), a menos que você informe uma
chave.

---

## 1. Pré-requisitos

| Item | Observação |
|---|---|
| Linux, macOS ou Windows com **WSL2** | No Windows, rode tudo dentro do Ubuntu do WSL2, não no PowerShell. |
| **Docker** com Compose v2 | Docker Desktop (macOS/Windows) ou Docker Engine (Linux). Precisa estar aberto/rodando. |
| `git`, `make`, `curl` | No Ubuntu: `sudo apt install -y git make curl`. No macOS vêm com as ferramentas do Xcode. |
| 4 GB de RAM livres e ~5 GB de disco | São 7 contêineres (Caddy, app, worker, scheduler, Postgres, Redis, backup). |
| Opcional: **uv** | Só para rodar os testes automáticos (`--testes`): `curl -LsSf https://astral.sh/uv/install.sh \| sh`. |

---

## 2. Instalação (um comando depois de baixar)

```bash
git clone https://github.com/danzeroum/rental-desk-ai.git
cd rental-desk-ai
bash scripts/teste-local.sh
```

Opções:

- `bash scripts/teste-local.sh --testes` → também roda lint, a suíte completa (~750 testes, ~8 min) e o
  conjunto de ouro (100 conversas).
- `bash scripts/teste-local.sh --email voce@exemplo.com` → operador com o seu e-mail
  (padrão: `operador@rentaldesk.local`).
- `LLM_API_KEY=sk-ant-... bash scripts/teste-local.sh` → liga a IA real (Anthropic) no lugar do
  FakeLLM. A chave vai só para o `.env` local, nunca para o git.

### O que o script faz, passo a passo

1. **Confere os programas** (git, Docker, Compose v2, make, curl) e se o Docker está rodando.
2. **Escolhe as portas:** 80/443 se estiverem livres; senão 8080/8443 (o painel fica em
   `https://localhost:8443`).
3. **Gera o `.env`** com `deploy/gen-env.sh`: senhas do banco, do Redis, chave de sessão e a chave AES
   que cifra tokens e segredos, todas aleatórias. O arquivo fica com permissão 600 e está no
   `.gitignore`. Se o `.env` já existe, ele é mantido.
4. **Constrói a imagem e sobe os serviços:** Postgres e Redis primeiro, depois as migrações do banco
   (`alembic upgrade head`, 9 migrações), depois todo o resto. Espera o `/health` responder pelo Caddy
   em HTTPS.
5. **Cria o operador** (você) e imprime o **convite**: o link onde você cria a senha. Cria as
   **imobiliárias de demonstração**: Demo Riviera (10 casas, para o painel), Maré Alta (8 casas) e
   Pousada Recanto da Enseada (6 quartos), as duas últimas para o kit de vendas.
6. **Cria um link do kit de demonstração** para vendas.
7. **Testes**, se você pediu `--testes`.
8. **Grava `ACESSO-LOCAL.md`** (fora do git) com o painel, o convite, o link da demo e os próximos
   passos. A saída detalhada de cada comando fica em `teste-local.log`.

Pode rodar o script de novo quando quiser: ele não apaga dados, renova a demonstração e gera um convite
e um link de demo novos.

### Certificado

O Caddy usa um certificado local para `localhost`, então o navegador avisa que a conexão não é
confiável. Clique em "Avançado" → "Continuar para localhost". Isso é só no teste local; no servidor, o
Caddy pega um certificado válido sozinho.

---

## 3. Primeiro acesso

1. Abra o **convite** que está em `ACESSO-LOCAL.md` (vale 72 h e funciona uma vez só).
2. Crie a senha: no mínimo 12 caracteres. Uma frase funciona bem.
3. **Cadastre o autenticador**: escaneie o QR com o Google Authenticator, o Authy ou o 1Password e
   digite o código de 6 dígitos. O login com senha + código é obrigatório para todos (SPEC, seção 5).
4. Você cai na **área do operador**.

Esqueceu a senha? Use `/senha/esqueci`. Sem SMTP, o e-mail não sai: rode o script de novo para
receber um convite novo. Perdeu o autenticador?

```bash
make reset-totp EMAIL=operador@rentaldesk.local
```

---

## 4. Roteiro de teste guiado (~45 min)

### 4.1 Área do operador (`/operador`) · M7, M8

- **Saúde:** uma linha por imobiliária, com situação (ok/alerta/erro e o motivo), última mensagem,
  datas em dia, passagens abertas, qualidade do número e passo da implantação. Os erros aparecem
  primeiro.
- **Demonstração** (`/operador/demo`): crie um link com um rótulo, copie (ele aparece uma vez só) e
  revogue. Depois de revogado, o link e as visitas abertas param na hora.
- **Minhas imobiliárias** → **Entrar como suporte** na *Imobiliária Demo Riviera*: você entra no painel
  da imobiliária com o papel de operador, e isso fica registrado no `audit_log`.

Código: `app/web/routes_operator.py`, `app/workers/health.py`, `app/domain/health.py`.

### 4.2 Painel da imobiliária (como suporte na Demo Riviera)

Os itens do menu, na ordem:

| Menu | O que testar | Requisitos / código |
|---|---|---|
| **Início** | Resumo: assistente ligado ou pausado, datas em dia, passagens aguardando, versão da planilha. | `routes_panel.py` |
| **Passagens** | Conversas que o assistente passou a um corretor: motivo, resumo do pedido, assumir pelo painel, registrar o desfecho (fechou/perdeu e por quê). Faça antes o kit (4.4) na Maré Alta e veja a passagem aparecer aqui, como suporte da Maré Alta. | RF12–RF17 · `app/workers/handoffs.py` |
| **Datas** | Bloqueios de casas: criar, ver origem (planilha, painel, calendário, WhatsApp do corretor), confirmar as datas do dia. A noite da saída fica livre. | RF10, RF20 · `routes_panel.py` |
| **Meus avisos** | Aceite dos avisos por WhatsApp (passagem, lembrete das 9h, resumo semanal) e o botão para desligar o resumo semanal por e-mail. Sem aceite, nada sai. | SPEC 9, RF18, RF32 |
| **Resultados** | Hoje, 7 e 30 dias: contatos, passagens, fechamentos, valor das opções enviadas e gráfico de contatos por dia. Só números agregados. | RF30 · `routes_results.py` |
| **Planilha** | Baixe o **modelo** (`/painel/dados/modelo`), envie de volta, veja a pré-visualização com erros por célula e os avisos, aprove e confira as versões. O assistente só usa a versão aprovada. Teste uma planilha com erro, por exemplo um período de tarifa sobreposto. | RF04–RF07 · `app/importer/` |
| **Calendários** | Cole um link iCal (Airbnb/Booking) por casa: o sistema lê a cada 15 min, e com todas as casas cobertas as datas ficam em dia sozinhas. Ligar uma planilha Google exige a conta de serviço (pendência). | RF26, RF27 · `app/workers/calendars.py`, `sheets.py` |
| **Implantação** | Os 7 passos da implantação, com o progresso calculado do estado real. O passo **Simulador** abre uma conversa de teste com os dados da imobiliária. | RF28 · `routes_onboarding.py` |
| **Situação** | Modo sombra: o assistente responde só para você ver, sem enviar ao hóspede, e ativar exige pelo menos uma resposta registrada. Mostra as últimas respostas que ele daria. | RF24 · `routes_shadow.py` |
| **Titulares** | Direitos do titular (LGPD): busque um telefone, exporte tudo o que há dele, anonimize. Os contatos que pediram PARAR aparecem aqui. | RF21, RF22 · `app/workers/subject_rights.py` |
| **Configurações** | Nome e tom do assistente, horário dos corretores, pausa geral, equipe (convidar corretor/gestor), escala de plantão por corretor. | RF11, RF19, RF29 |

### 4.3 Simulador: o "cérebro" do produto

No **Simulador**, escreva como um hóspede. Sugestões para a Demo Riviera:

1. `Casa para 10 pessoas de 28/12 a 02/01 com piscina`: cartões com o **pacote Réveillon**, valores
   em reais, datas e, sempre, o aviso "Valores estimados e disponibilidade sujeitos à confirmação do
   corretor".
2. `a segunda tem churrasqueira?`: resposta direta da planilha. Se o dado não existe, "vou confirmar
   com o corretor"; o assistente nunca inventa.
3. `para 6 pessoas de 10/01 a 12/01`: mínimo de noites e período que cruza tarifas.
4. `quero a primeira` ou `quero falar com um corretor`: passagem.
5. `consegue um desconto?`: negociação vai para o corretor, a IA não negocia.
6. `PARAR` (no kit, 4.4): a confirmação combinada e silêncio total. `VOLTAR` reativa.

**Regras que estão sendo testadas** (CLAUDE.md, regras inegociáveis):

- **A IA nunca decide fatos.** O LLM só extrai a intenção (JSON com esquema) e escreve duas frases
  curtas, sem números. Casa, preço, data e disponibilidade vêm do banco
  (`app/domain/pricing.py`, `search.py`).
- **Cartões só por template Jinja** (`app/domain/render.py`). Um **validador**
  (`app/domain/validator.py`) recusa qualquer frase do LLM com número, data, valor, código de casa ou
  link e a troca por um texto fixo.
- **Sem dados pessoais no LLM nem nos logs:** nome e telefone não vão para a IA; CPF, cartão, e-mail
  e Pix são mascarados (`app/core/masking.py`).
- **Toda resposta grava `audit_log`** encadeado por hash, que não pode ser alterado nem apagado
  (trigger no Postgres).

### 4.4 Kit de demonstração para vendas · `/demo`

Abra o link de demonstração do `ACESSO-LOCAL.md`, de preferência no celular. Na mesma rede Wi-Fi,
troque `localhost` pelo IP do computador; o celular também vai avisar do certificado.

1. Escolha a **Imobiliária Maré Alta**.
2. Toque em "Casa para 8 pessoas de 28/12 a 02/01 com piscina": aparece "digitando…" (o debounce
   espera o hóspede terminar de escrever), depois os cartões.
3. "Quero a primeira": a tela ao lado (no celular, mais abaixo) mostra o **WhatsApp do corretor**
   recebendo o aviso "Nova passagem… Responda 1".
4. Toque em **Responder 1**: o corretor assume, e o assistente fica em silêncio nessa conversa por 24 h.
5. "Recomeçar" gera um hóspede novo. Repita com a **Pousada Recanto da Enseada** (6 quartos).
6. Como operador, entre como suporte na Maré Alta → **Passagens**: a passagem está lá, assumida
   "pelo WhatsApp".
7. Em `/operador/demo`, revogue o link: a tela da demo passa a dizer "link expirado ou revogado".

Por baixo, a mensagem do visitante segue exatamente o caminho de uma mensagem do webhook do WhatsApp
(fila → worker → motor → envio). Só o envio é desviado: os números `demo-…` gravam no Redis em vez de
chamar a Meta. Todo dia às 3h as conversas das imobiliárias de demonstração são apagadas.

Detalhes: `docs/DECISIONS.md` D045 e D046; código em `app/web/routes_demo.py`,
`app/workers/sales_demo_chat.py`, `app/adapters/demo_gateway.py`.

### 4.5 Páginas públicas

- `/ajuda`: central de ajuda (primeiros passos, planilha, calendários, passagens, perguntas).
- `/termos` e `/politica-de-privacidade`: textos-base, **pendentes de revisão jurídica**.
- `/status`: saúde pública, sem detalhe interno; responde 503 se algo cair.
- `/privacidade/imobiliaria-demo-riviera`: o aviso de privacidade que o assistente manda na primeira
  resposta.

### 4.6 Operação (terminal)

```bash
make ps
```

```bash
make logs
```

```bash
make backup
```

```bash
make restore-drill
```

- `make ps` mostra os 7 serviços, saudáveis.
- `make logs` mostra os logs em JSON. Repare que não há telefone, e-mail nem token neles.
- `make backup` gera um backup cifrado agora; o automático roda às 3h.
- `make restore-drill` restaura o último backup num banco à parte, confere e mede o tempo.

Outros comandos úteis:

```bash
make tenant NAME="Imobiliária Teste" EMAIL=gestor@exemplo.com
```

```bash
make demo-link LABEL="visita à Imobiliária X"
```

```bash
make down
```

- `make tenant` cria uma imobiliária nova e imprime o convite do gestor. Faça a implantação inteira
  pelo painel: é o teste de aceite do M10.
- `make demo-link` cria outro link do kit.
- `make down` para tudo e mantém os dados; `make up` sobe de novo.

Para apagar **tudo** (dados inclusive) e recomeçar do zero:

```bash
docker compose --project-directory . -f deploy/docker-compose.yml --env-file .env down -v
```

---

## 5. Testes automáticos

```bash
bash scripts/teste-local.sh --testes
```

Ou, já com o sistema instalado, um por vez:

```bash
make dev
```

```bash
make test-deps-up
```

```bash
make lint
```

```bash
make test
```

```bash
make golden
```

```bash
make test-deps-down
```

- `make dev` instala as dependências com o uv.
- `make test-deps-up` sobe Postgres e Redis de teste (portas 55432 e 56379).
- `make lint` roda ruff, mypy e shellcheck.
- `make test` roda unidade, integração e sistema, com cobertura; o mínimo nos módulos centrais é 90%.
- `make golden` roda 100 conversas de ouro com o FakeLLM.
- `make test-deps-down` derruba o Postgres e o Redis de teste.

| Pasta | O que cobre |
|---|---|
| `tests/unit` | Domínio puro: preço (período que cruza tarifas, pacote parcial, mínimo de noites, check-in = check-out), busca, decisão, validador, máscara, horário, iCal. |
| `tests/integration` | Banco real com RLS: isolamento entre imobiliárias, webhook com assinatura, fila, passagem, painel (login, CSRF), planilha, kit de demonstração. |
| `tests/system` | Percursos completos: implantação do zero até "atendendo", falhas (Redis/Postgres fora), backup e restauração. |
| `tests/golden` | 65 conversas de fluxo + 35 adversárias: zero fato fora da base, 100% dos cartões com aviso, passagem correta. |
| `tests/load` | 200 conversas em 10 imobiliárias (`make load`; resultado em `docs/carga.md`). |

Com a chave da IA, `make golden-live` compara os modelos no conjunto de ouro. É o item 2 da definição
de pronto da v1.0.0.

---

## 6. Como as peças se encaixam

```
Hóspede (WhatsApp) ──► Meta / parceiro (BSP) ──► Caddy (HTTPS) ──► app: /webhook (assinatura conferida)
                                                                      │ grava a mensagem (RLS)
                                                                      ▼
                                                    Redis: debounce + limite anti-abuso
                                                                      │
                                                                      ▼
   worker: extração (LLM → JSON) → estado → motor (preço/busca no Postgres) → cartões Jinja
           → validador → envio pelo gateway → audit_log → passagem ao corretor, se for o caso
                                                                      │
   scheduler: escalonamento, lembrete das 9h, relatório semanal, alertas, calendários,
              planilha Google, recuperação de mensagens paradas, limpeza da demo
```

- **Multi-imobiliária seguro:** toda tabela de negócio tem `tenant_id`, com **RLS forçado** no
  Postgres. O app usa um papel sem `BYPASSRLS`; só o scheduler tem um papel de leitura global
  (`app/core/rls.py`, `migrations/`).
- **Adaptadores com versão falsa** (`app/adapters/`): WhatsApp, IA, transcrição, e-mail, calendários,
  planilha Google. O provedor real é escolhido no `.env`, sem nada fixo no código.
- **Estrutura:** `app/domain` (regras puras, sem I/O), `app/core` (config, banco, RLS, segurança,
  auditoria, máscaras), `app/workers` (pipeline), `app/scheduler`, `app/web` (painel HTMX, sem SPA),
  `app/importer` (planilha).

---

## 7. Problemas comuns

| Sintoma | Solução |
|---|---|
| "o Docker não está rodando" | Abra o Docker Desktop ou rode `sudo systemctl start docker`. No Linux sem sudo: `sudo usermod -aG docker $USER` e faça logout/login. |
| Porta 443 ocupada | O script passa sozinho para 8443. Use `https://localhost:8443` (não `http://localhost:8080`, que redireciona para a 443). |
| Contêiner "unhealthy" | `make logs`. Falta de memória no Docker Desktop: aumente para 4 GB ou mais em Settings → Resources. |
| Convite expirado ou já usado | Rode o script de novo: ele imprime um convite novo. |
| Windows: `bash\r` ou `$'\r'` | Clonou no Windows com conversão de linha. Clone de novo dentro do WSL2. |
| Quer começar do zero | O `down -v` da seção 4.6, depois `rm .env` e o script de novo. |

---

## 8. Onde está cada documento

| Documento | Conteúdo |
|---|---|
| `docs/SPEC.md` | A especificação (fonte da verdade): requisitos RF01–RF32, regras, marcos. |
| `docs/PROGRESS.md` | O que foi entregue em cada marco, como foi verificado e o que depende de você. |
| `docs/DECISIONS.md` | Decisões D001–D046 (contexto, escolha, alternativas) e pontos de extensão para outros segmentos. |
| `docs/lancamento.md` | Definição de pronto da v1.0.0, **ordem das suas pendências** e o kit de vendas. |
| `docs/runbook.md` | Operação em produção: instalação, backup, restauração, alertas, incidentes. |
| `docs/seguranca.md`, `docs/carga.md` | Revisão de segurança e resultados do teste de carga. |
| `docs/whatsapp-templates.md` | Os 5 templates a submeter à Meta. |
| `docs/verificacao-coexistencia.md` | Roteiro para testar o número em coexistência com o app WhatsApp Business. |
| `CHANGELOG.md` (em `docs/`) | O que mudou em cada versão. |
