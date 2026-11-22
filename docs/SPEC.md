# Especificação — rental-desk-ai · Versão 1.0 do produto (revisão 0.3 da especificação)

Central de reservas com IA no WhatsApp para imobiliárias de aluguel por temporada.
Este arquivo é a **fonte da verdade** do produto. Se algo aqui conflitar com outro documento, vale este.

**Não é protótipo.** O que este documento descreve é a Versão 1.0: o produto que vai a cliente pagante, com qualidade de produção, suporte e contrato. Versões seguintes (1.1, 1.2…) **acrescentam** funções; nada da 1.0 é feito para ser jogado fora.

---

## 0a. O que mudou na revisão 0.3 (de "MVP" para Versão 1.0)

| Mudança | Motivo |
|---|---|
| Fim do termo "MVP": o escopo abaixo é a **Versão 1.0 vendável** | O primeiro cliente recebe o produto definitivo, não um teste |
| **Sincronização automática de calendário** (iCal do Airbnb/Booking/sistema da imobiliária) e **planilha Google sincronizada** entram na 1.0 (RF26, RF27) | Dados de disponibilidade atualizados sem trabalho manual são o que faz o produto funcionar no dia a dia |
| **Assistente de implantação no painel** (RF28): conectar WhatsApp, enviar ou ligar planilha, conversar em teste, aprovar | "Baixa e funciona": a imobiliária ativa sozinha, com você só acompanhando |
| **Vários corretores com plantão e rodízio** (RF29) | Imobiliária real tem mais de um corretor |
| **Painel de resultados** em tempo real (RF30), além do relatório semanal | O gestor vê o valor do produto todo dia |
| **Central de ajuda, termos de uso, política de privacidade e página de status** (RF31) | Requisitos de produto vendido, não de protótipo |
| Relatório também por WhatsApp ao gestor, com consentimento (RF32) | Gestor lê no celular |
| **TOTP para todos os usuários do painel**; homologação em **VPS separada**; conjunto de ouro de **100 casos antes do lançamento** | Restaurado o nível de produção que tinha sido cortado para ganhar prazo |
| Marcos M9 a M11 e **definição de pronto da Versão 1.0** (seção 12) | Critério objetivo para dizer "está pronto para vender" |
| Login do painel: **e-mail + senha + TOTP para todos**; o link mágico da v0.2 sai. O `Mailer` serve a convite de usuário, recuperação de senha e relatório semanal (esclarecimento de 21/11/2026) | Mesmo nível de segurança para gestor, corretor e operador |
| O "teste de coexistência" continua, mas é **verificação técnica de 1 hora** de uma dependência externa da Meta, não prova de conceito do produto | Não dá para escrever código contra um comportamento da Meta que ninguém confirmou |

---

## 0b. O que mudou da v0.1 para a v0.2

| Mudança | Motivo |
|---|---|
| Cards de imóveis montados por **template (Jinja)**; a IA só escreve frases curtas sem números | Elimina alucinação estrutural e simplifica o validador |
| **Debounce** de mensagens no Redis (6 s após a última, máximo 20 s) | Hóspede manda mensagens picadas; evita respostas atropeladas |
| **Estado de conversa** persistido (`conversation.state_json`) | Perguntas de acompanhamento ("a segunda tem piscina aquecida?") |
| **Row-Level Security** no Postgres, além do filtro na aplicação | Defesa em profundidade contra vazamento entre imobiliárias |
| Regras explícitas de **tarifa, noites, pacote e ranking** | Evita erros de valor no Réveillon e resultados em ordem arbitrária |
| Dinheiro em **centavos (inteiro)**, check-out **exclusivo**, fuso **America/Sao_Paulo** | Elimina arredondamento e erro de uma noite |
| Acesso ao WhatsApp por **parceiro oficial (BSP) com coexistência** no MVP; gateway compatível com a Cloud API para migrar à Meta direta depois | Não travar o lançamento na aprovação de provedor de tecnologia |
| **Assumir conversa** primário pelo aviso ao corretor ("responda 1") e pelo painel; silêncio automático por eco só depois de provado | Não prometer algo que a plataforma pode não entregar. `#assumir` digitado na conversa iria aparecer para o hóspede |
| Transcrição de áudio por **API sem uso para treino**; áudio apagado logo após transcrever | Não travar a VPS no pico; o texto já vai para a IA de qualquer forma |
| Novos requisitos: **desfecho da passagem**, **botão de pausa**, **limite anti-abuso**, **avisos de consistência na importação**, **tempo de resposta humana**, **escalonamento de passagem**, **modo demonstração** | Medir resultado (renovação), segurança operacional e venda antes da aprovação da Meta |
| MVP enxuto: login do gestor por link mágico (TOTP só para operador), homologação no mesmo host, conjunto de ouro inicial com 40 casos, **modo sombra** no lugar do modo assistido com tempo-limite | Entregar até 13/12 sem prejudicar o atendimento noturno |
| RTO ≤ 4 h e RPO ≤ 24 h declarados | Recuperação com alvo claro |

Itens **não** aceitos das revisões: fixar um modelo de IA específico no código (fica configurável e é escolhido pelo conjunto de ouro); afirmar que a Meta envia eco das mensagens do app sem testar (fica como prova de conceito no M0); avaliação semanal por "IA juiz" (fase 3).

---

## 1. Objetivo e escopo

**Objetivo:** responder no WhatsApp da imobiliária, em segundos e 24 h, pedidos de reserva de casas de temporada, usando **só dados aprovados pela imobiliária**, e passar o cliente ao corretor para fechar.

**Sucesso do produto:** primeira resposta em menos de 1 min; nenhum fato inventado no conjunto de ouro; relatório semanal mostrando atendimentos fora do horário **e quantas passagens viraram reserva**.

**"Pronto para usar":** a imobiliária não instala nada. Ela conecta o WhatsApp que já usa (link de cadastro do parceiro, em coexistência), preenche a planilha modelo, sobe no painel e aprova. O sistema roda numa VPS do operador, que sobe com um comando.

**Dentro da Versão 1.0:** texto e áudio em português; busca por datas, pessoas, atributos e orçamento; até 3 opções com valor estimado; respostas sobre a casa só a partir dos dados aprovados; passagem ao corretor com plantão e rodízio; sincronização de calendário (iCal) e de planilha Google; assistente de implantação; painel do gestor (resultados, dados, passagens, desfechos) e do operador (saúde, clientes); lembrete diário; relatório semanal por e-mail e WhatsApp; trilha de auditoria; modo demonstração; central de ajuda, termos, política de privacidade e página de status.

**Fora da Versão 1.0, por decisão de produto (não por falta de tempo):** confirmar reserva, cobrar sinal ou negociar preço — sempre humano, por segurança jurídica e porque é o trabalho do corretor; disparos de marketing — exigem consentimento específico e outro desenho de produto.

**Planejado para versões seguintes:** integração direta por API com sistemas de gestão (Stays e similares) — 1.1; atendimento completo em inglês e espanhol — 1.1; caixa de entrada web compartilhada (Chatwoot) para imobiliárias grandes — 1.2; mensagens de reativação de feriado com consentimento — 1.2.

---

## 2. Atores

| Ator | Papel |
|---|---|
| Hóspede | Escreve ou manda áudio no WhatsApp da imobiliária |
| Corretor | Recebe aviso de passagem no WhatsApp pessoal (opt-in), assume e fecha pelo app WhatsApp Business |
| Gestor | Sobe e aprova a planilha, atualiza datas, informa desfechos, recebe relatório |
| Operador | Dono do produto: cadastra imobiliárias, monitora, dá suporte |
| Tarefas agendadas | Lembrete 9h, relatório semanal, escalonamentos, retenção, backups |

---

## 3. Arquitetura

- **Monólito modular** em Python 3.12 + FastAPI: processo `app` (webhook, painel, API interna) e processo `worker` (fila), mais `scheduler`.
- **PostgreSQL 16** (fonte da verdade, RLS ativo) e **Redis 7** (fila, debounce, rate limit, locks).
- **Caddy** na frente (HTTPS automático). Tudo em **Docker Compose**.
- **Multi-tenant único**: toda tabela de negócio tem `tenant_id`; RLS + filtro na aplicação.
- **Painel**: Jinja2 + HTMX, renderizado no servidor. Sem build de frontend.
- **Adaptadores** (interfaces com implementação fake para testes):
  - `WhatsAppGateway`: envio/recebimento no formato da Cloud API. URL base e cabeçalho de autenticação configuráveis, para funcionar com parceiro oficial (BSP) ou Meta direta.
  - `LLMClient`: extração (saída JSON com esquema) e redação curta. Provedor e modelo por variável de ambiente.
  - `Transcriber`: áudio → texto por API.
  - `Mailer`: e-mail de convite de usuário, recuperação de senha e relatório semanal. (Não há link mágico: o painel usa e-mail + senha + TOTP para todos os usuários.)
- **Proibido**: APIs não oficiais de WhatsApp (QR code / WhatsApp Web).

### Pipeline de cada mensagem recebida

1. Webhook valida assinatura → grava `message` (idempotente pelo ID externo) → responde 200 rápido.
2. **Debounce**: agenda o processamento da conversa para 6 s após a última mensagem (máximo 20 s desde a primeira do lote). Lock por conversa.
3. Carrega `tenant`, `conversation.state_json`. Checa: tenant pausado? contato com opt-out? conversa muda (corretor assumiu)? rate limit? → se sim, não responde (ou resposta fixa definida).
4. Áudio → `Transcriber` → texto (áudio original apagado após sucesso).
5. **Extração** (`LLMClient`, JSON com esquema): intenção + slots + referência a casa já oferecida ("a segunda"). Entrada inclui o estado atual e as últimas mensagens, **sem nome e telefone**, com mascaramento.
6. **Mescla** do resultado no `state_json` (código, não IA).
7. **Decisão** (código): perguntar o que falta | buscar | responder atributo | passar para humano | resposta fora de escopo.
8. **Busca e ranking** (SQL + Python), cálculo de valor (seção 6).
9. **Renderização**: cards por template Jinja (código, título, capacidade, valor estimado, observação de pacote, link das fotos, aviso legal). A IA só escreve **uma frase de abertura e uma de fechamento**, sem números, datas, valores ou códigos.
10. **Validador** (seção 7). Falhou → reescreve a frase uma vez → falhou de novo → usa frase fixa.
11. Envio pelo `WhatsAppGateway` → grava `message` de saída e `audit_log`.

---

## 4. Requisitos funcionais

| ID | Requisito | Critério de aceite |
|---|---|---|
| RF01 | Webhook oficial | Assinatura validada; ID externo repetido processado uma vez |
| RF02 | Identificação como assistente virtual | 1ª resposta de toda conversa diz que é assistente virtual da imobiliária e traz link do aviso de privacidade (versão registrada) |
| RF03 | Extração de intenção em JSON | JSON inválido nunca chega à decisão; campo ausente vira pergunta |
| RF04 | Estado de conversa | Pergunta de acompanhamento ("e a segunda?", "e se formos 10?") usa o estado e as últimas casas oferecidas |
| RF05 | Debounce | 5 mensagens em 10 s geram 1 resposta |
| RF06 | Busca e ranking determinísticos | Máx. 3 casas, ordem estável conforme seção 6 |
| RF07 | Cálculo de valor | Conforme seção 6; sempre rotulado como estimativa |
| RF08 | Cards por template | Valores, datas, códigos e aviso legal vêm só do template |
| RF09 | Atributos só com dado | Campo vazio → "vou confirmar com o corretor" + pendência no resumo |
| RF10 | Passagem para humano | Gatilhos da seção 7 → aviso ao corretor de plantão com resumo; hóspede informado |
| RF11 | Assumir conversa | Corretor responde "1" ao aviso **ou** clica "assumir" no painel → conversa muda por 24 h. Eco automático (mensagem do app detectada) só após prova no M0 |
| RF12 | Escalonamento | Contado em tempo comercial (horário dos corretores). Sem assumir em **10 min** → a passagem vai ao próximo corretor de plantão no rodízio, e segue girando a cada 10 min; em **15 min** → alerta ao gestor e ao operador; em **60 min** → mensagem ao hóspede com previsão de retorno e telefone de contato. Fora do horário dos corretores nada disso dispara: o hóspede recebe o horário de retorno e a passagem entra na fila do primeiro corretor do próximo plantão (esclarecimento de 21/11/2026) |
| RF13 | Dados vencidos | `ultima_atualizacao` > 24 h → nenhuma resposta afirma disponibilidade; cards dizem "disponibilidade a confirmar" |
| RF14 | Importação da planilha | Erros bloqueantes por aba/linha; **avisos não bloqueantes**: casa sem tarifa futura, link de fotos fora do ar (checagem HTTP), capacidade incoerente |
| RF15 | Aprovação | Nada vai ao ar sem aprovação registrada (quem, quando, hash do snapshot) |
| RF16 | Atualização de datas | Painel e resposta ao lembrete das 9h ("OK" renova; "CASA12 28/12-02/01" bloqueia) |
| RF17 | Desfecho da passagem | Gestor marca cada passagem: fechou / perdeu / pendente (painel ou pergunta semanal de 1 clique) |
| RF18 | Relatório semanal | Contatos, % fora do horário, 1ª resposta do assistente, passagens, **desfechos e taxa de fechamento**, tempo até o corretor assumir, soma dos valores estimados. Sem dados pessoais |
| RF19 | Pausa geral | Botão "pausar assistente" por imobiliária, efeito imediato, sem deploy |
| RF20 | Anti-abuso | > 15 mensagens/min de um contato → uma resposta educada e silêncio de 10 min; primeira mensagem de contato novo tem prioridade na fila |
| RF21 | Direitos do titular | Exportar e apagar dados por telefone (inclui logs e anexos); "PARAR" → opt-out |
| RF22 | Fora de escopo e idioma | Assunto fora de hospedagem → passagem; mensagem em outro idioma → resposta fixa bilíngue + passagem |
| RF23 | Painel do operador | Saúde por tenant: último webhook, erros, rejeições do validador, idade dos dados, fila de passagens, qualidade do número |
| RF24 | Configuração por tenant | Nome do assistente, horário dos corretores, corretores de plantão, telefone de contato, 1 de 2 tons. Nada além |
| RF25 | Modo demonstração | Tenant "Imobiliária Demo Riviera" com 10 casas fictícias, atendível por número de teste, para vendas |
| RF26 | Sincronização de calendário (iCal) | Gestor cola o link iCal de cada casa (Airbnb, Booking, Vrbo ou sistema próprio); sincroniza a cada 15 min; bloqueios importados com origem `ical`; link que falha 3 vezes gera alerta; dado sincronizado conta como "atualizado" para o RF13 |
| RF27 | Planilha Google sincronizada | Alternativa ao upload: gestor compartilha a planilha modelo no Google com a conta de serviço; sincroniza a cada 15 min; cada mudança gera novo snapshot; mudanças em preço ou atributo exigem aprovação no painel; bloqueios entram direto |
| RF28 | Assistente de implantação | Passo a passo no painel: (1) conectar WhatsApp pelo cadastro do parceiro em coexistência; (2) enviar ou ligar planilha e links iCal; (3) corrigir erros; (4) conversar com o assistente num simulador; (5) aprovar; (6) modo sombra de 2 dias; (7) ativar. Progresso salvo; o operador acompanha |
| RF29 | Corretores, plantão e rodízio | Cadastro de corretores (nome, WhatsApp pessoal, consentimento); escala de plantão por dia e horário; passagens distribuídas em rodízio entre os de plantão; se ninguém assumir em 10 min comerciais, vai ao próximo, e continua girando a cada 10 min; gestor sempre em cópia. Regras de tempo e fora do horário: ver RF12 (esclarecimento de 21/11/2026) |
| RF30 | Painel de resultados | Gráficos do dia, semana e mês: contatos, fora do horário, tempo de resposta, passagens, tempo até o corretor assumir, desfechos, taxa de fechamento, valor estimado das opções enviadas |
| RF31 | Páginas de produto | Central de ajuda (como preencher a planilha, como assumir, perguntas frequentes), termos de uso, política de privacidade e página de status pública do serviço |
| RF32 | Relatório por WhatsApp | Resumo semanal de 5 linhas enviado ao WhatsApp do gestor com consentimento registrado; link para o painel |

---

## 5. Requisitos não funcionais

| Atributo | Meta |
|---|---|
| Tempo de resposta | Texto: p95 ≤ 15 s **após** a janela de debounce. Áudio: p95 ≤ 25 s |
| Disponibilidade | 99,5% entre 15/12 e 15/02 |
| Recuperação | RTO ≤ 4 h; RPO ≤ 24 h; simulação trimestral |
| Capacidade | 20 tenants; picos absorvidos pela fila sem perda |
| Exatidão | Zero fatos fora da base no conjunto de ouro |
| Segurança | TLS; segredos só em env; RLS; e-mail + senha + **TOTP obrigatório para todos os usuários do painel**; bloqueio após tentativas; disco e backup cifrados; tokens de terceiros cifrados no banco |
| Privacidade | Minimização; reten­ção por classe de dado (seção 9); dados em repouso na VPS no Brasil |
| Manutenibilidade | Cobertura ≥ 80% em `pricing`, `search`, `validator`, `importer`, `state`; `make up` sobe tudo |
| Observabilidade | Logs JSON com `tenant_id`, `conversation_id`, `trace_id`, sem dados pessoais; métricas de latência, erros, rejeições, filas |
| Custo | Infra + IA ≤ R$ 300 por tenant/mês (validar) |

---

## 6. Modelo de dados e regras de negócio

### Convenções

- Dinheiro: inteiro em **centavos** (`*_cents`). Nunca float.
- Datas de estadia: `date`. Noites = de `check_in` (inclusive) a `check_out` (**exclusive**).
- Fuso: `America/Sao_Paulo` para tudo que é "hoje", "9h", "24 h".
- IDs: UUID. Timestamps: `timestamptz`.

### Tabelas (todas as de negócio com `tenant_id` + RLS)

`tenant`, `user` (papel: operador | gestor | corretor; implementado como `app_user` + `tenant_member`, ver DECISIONS D011), `property`, `rate`, `block`, `data_snapshot`, `contact`, `conversation` (inclui `state_json`, `muted_until`, `status`), `message`, `intent`, `search_result`, `handoff` (inclui `outcome`, `assumed_at`, `assumed_by`), `audit_log` (append-only, `prev_hash` + `hash`), `privacy_notice` (versionado), `weekly_report`, `consent` (opt-in do gestor/corretor para mensagens ativas).

RLS: `SET LOCAL app.current_tenant_id` no início de cada transação; política `tenant_id = current_setting('app.current_tenant_id')::uuid`. O papel de banco da aplicação **não** tem `BYPASSRLS`. Tarefas globais do operador usam papel separado e explícito.

### `conversation.state_json`

```json
{
  "slots": {"check_in": null, "check_out": null, "guests": null, "budget_max_cents": null,
            "neighborhood": null, "required": [], "pets": null},
  "last_offered": ["CASA12", "CASA07", "CASA03"],
  "pending_questions": [],
  "clarification_attempts": 0,
  "language": "pt"
}
```

### Esquema da extração (saída do LLM)

```json
{
  "intent": "search | ask_attribute | choose | negotiate | complaint | payment | cancel | human | smalltalk | out_of_scope | other_language",
  "slots_update": {"check_in": "YYYY-MM-DD|null", "check_out": "YYYY-MM-DD|null", "guests": "int|null",
                   "budget_max_brl": "int|null", "neighborhood": "string|null", "required": ["piscina|pet|ar_quartos|wifi|churrasqueira|..."]},
  "refers_to": "codigo|ordinal(1-3)|null",
  "attribute_asked": "string|null"
}
```

### Valor estimado

1. Para cada noite, achar a tarifa (`rate`) cujo período cobre a noite. Alguma noite sem tarifa → casa **não cotável** (sai do resultado; aparece como aviso no painel).
2. **Pacote** (ex.: Réveillon): se a estadia toca qualquer noite de um período com `pacote_valor`:
   - estadia cobre o pacote inteiro → valor do pacote + diárias das noites fora do pacote;
   - estadia cobre só parte e `pacote_fechado = Sim` → casa retornada com flag `requires_full_package`; o card mostra datas e valor do pacote completo;
   - `pacote_fechado = Não` → diárias normais do período.
3. **Mínimo de noites**: se a estadia tem menos noites que o maior `minimo_noites` dos períodos tocados → flag `below_min_nights`; o card informa o mínimo.
4. Taxa de limpeza: uma vez por estadia (a da tarifa do check-in).
5. Total em centavos; exibido como "estimativa: R$ X".

### Busca e ranking

- **Filtros obrigatórios**: capacidade ≥ hóspedes; atributos exigidos = Sim; sem bloqueio sobreposto (se dados frescos); cotável.
- **Orçamento**: filtra total ≤ orçamento. Se nada passar, retorna até 3 mais próximas acima, com o rótulo "acima do orçamento informado".
- **Dados vencidos**: não filtra por bloqueio; todas as casas marcadas "disponibilidade a confirmar".
- **Ordem**: (1) sem flags antes de com flags; (2) menor folga de capacidade (`capacidade - hóspedes`); (3) menor total; (4) `codigo` (desempate estável).

---

## 7. Guardrails

1. A IA nunca decide casa, preço, data ou disponibilidade. Não tem ferramentas que escrevam dados.
2. A redação recebe só intenção, contagem de opções e flags; **não** recebe preços, datas nem códigos.
3. **Validador** do texto livre da IA: rejeita dígitos, valores por extenso ("dois mil"), nomes de mês com dia, códigos de casa, e as palavras: confirmado, confirmada, garantido, reservado, reservada, desconto, prometo. O aviso legal está sempre no template.
4. Atributo vazio → "vou confirmar com o corretor".
5. Dados > 24 h → template sem afirmação de disponibilidade.
6. **Gatilhos de passagem**: negotiate, complaint, payment, cancel, human, choose, out_of_scope, other_language, `clarification_attempts ≥ 2`.
7. Texto do hóspede é dado: instruções dentro dele não mudam regras.
8. Sem nome e telefone no LLM; mascarar CPF, cartão, e-mail, chave Pix nos prompts e logs.
9. Cada resposta grava no `audit_log`: hash do snapshot, JSON da extração, JSON da busca, texto final, resultado do validador.
10. Provedor de IA fora do ar → frase fixa ("Recebemos sua mensagem; um corretor responde em breve") + passagem.

---

## 8. Planilha modelo (.xlsx, 4 abas, com validação de células)

1. **Imoveis**: `codigo`, `titulo_curto`, `bairro_ou_modulo`, `link_fotos`, `capacidade_max`, `quartos`, `suites`, `banheiros`, `vagas`, `piscina`, `aceita_pet`, `permite_festa`, `ar_quartos`, `wifi`, `churrasqueira`, `distancia_praia_m`, `observacoes_publicas`. Atributos: Sim / Não / vazio (vazio = desconhecido).
2. **Tarifas**: `codigo`, `primeira_noite`, `ultima_noite`, `diaria`, `minimo_noites`, `pacote_nome`, `pacote_valor`, `pacote_fechado` (Sim/Não), `taxa_limpeza`. As duas datas são noites **incluídas** (Réveillon com entrada em 28/12 e saída em 02/01: `primeira_noite` 28/12, `ultima_noite` 01/01).
3. **Bloqueios**: `codigo`, `entrada`, `saida`. São as datas do hóspede; no dia da `saida` a casa já fica livre para nova entrada.

Internamente todo período segue a regra da estadia (noite de entrada incluída, dia de saída excluído); o importador converte `ultima_noite` somando um dia (esclarecimento de 22/11/2026, DECISIONS D016).
4. **Regras**: chave/valor curto (check-in, check-out, caução, pagamento, cancelamento).

O sistema gera o arquivo modelo (`make template` e botão no painel).

---

## 9. Segurança e LGPD (validar com advogado)

- Papéis: imobiliária **controladora**; operador **operador**; suboperadores: parceiro de WhatsApp/Meta, provedor de IA, provedor de transcrição, VPS, backup.
- Bases legais propostas: atendimento = procedimentos preliminares de contrato a pedido do titular; métricas agregadas = legítimo interesse; mensagens ativas ao gestor/corretor = consentimento registrado em `consent`.
- Dados sensíveis espontâneos (saúde, acessibilidade, religião): não estruturar, mascarar em log, não usar. Crianças: só a quantidade.
- **Retenção (configurável por classe)**: áudio bruto apagado após transcrição; mensagens com nome/telefone anonimizados após 180 dias sem disputa aberta; `audit_log` + snapshot mantidos por 5 anos (prazo a confirmar com advogado); logs técnicos 30 dias. Rotina diária executa e registra.
- Incidente: detectar → conter → avaliar → avisar controladora em até 24 h → registrar.
- Aviso de privacidade versionado e imutável por versão.

---

## 10. Implantação e operação

- `install.sh` em VPS Ubuntu LTS limpa: instala Docker, gera segredos no `.env`, pede domínio, sobe, roda migrações, cria operador.
- `docker-compose.yml`: caddy, app, worker, scheduler, postgres, redis, backup. **Homologação em VPS separada**, instalada pelo mesmo `install.sh` com `ENV=staging`.
- Versionamento semântico (1.0.0, 1.0.1, 1.1.0…), `CHANGELOG.md` e notas de versão no painel do operador.
- Backup diário cifrado para armazenamento externo (S3 compatível), 30 dias; `make restore` testado.
- CI (GitHub Actions): lint (ruff), tipos (mypy), testes, cobertura, SAST (semgrep ou bandit), dependências (pip-audit), segredos (gitleaks), build de imagem. Deploy em produção manual; rollback por tag de imagem.
- Congelamento: 20/12–02/01 e Carnaval, só correções urgentes.
- Monitor externo e alertas: fila acumulada, erros de IA, rejeições do validador, disco > 80%, tenant sem webhook, dados vencidos, qualidade do número.

---

## 11. Testes

- Níveis: unidade, integração, sistema, aceitação; separar **verificação** (está correto?) de **validação** (resolve o problema da imobiliária?).
- TDD em `pricing`, `search`, `validator`, `importer`, `state`.
- **Conjunto de ouro: 100 casos antes do lançamento da 1.0** (65 fluxos + 35 adversários) em `tests/golden/*.yaml`, executados contra o pipeline com `FakeLLM` determinístico **e** contra o LLM real (job separado). Cresce com conversas reais anonimizadas a cada versão.
- Critérios de produção: zero fatos fora da base; 100% dos cards com aviso; passagem correta ≥ 95%; nenhuma afirmação de reserva.
- Teste de isolamento entre tenants (inclui tentativa de leitura cruzada com RLS).
- Carga: 200 conversas simultâneas em 10 tenants sem perda e dentro do p95.
- Falha: LLM, Redis e Postgres indisponíveis em staging.
- **Modo sombra** (cada novo cliente, 2 dias): o sistema roda sobre as conversas reais e grava a resposta que daria, sem enviar; o gestor e o operador comparam e então ativam. Não testa o produto: confere se **os dados daquele cliente** (planilha, calendários) estão certos antes de o hóspede ver qualquer coisa.

---

## 12. Marcos de implementação

| Marco | Entrega | Aceite |
|---|---|---|
| M0 | Esqueleto: repo, compose, Makefile, CI, `install.sh`, health check; roteiro da **verificação técnica de coexistência** (`docs/verificacao-coexistencia.md`, executada pelo humano em ~1 hora) | `make up` sobe; CI verde; roteiro pronto |
| M1 | Modelo de dados, migrações, RLS, teste de isolamento | Teste cross-tenant falha ao ler outro tenant |
| M2 | Gerador e importador da planilha, snapshot, aprovação, avisos | Planilha de exemplo importa; erros/avisos por linha |
| M3 | `pricing` + `search` por TDD | Todos os casos da seção 6 cobertos |
| M4 | Webhook, gateway, debounce, rate limit, transcrição, estado | Rajada de 5 mensagens → 1 processamento |
| M5 | Extração, decisão, templates, validador, auditoria, contingência | Conjunto de ouro com FakeLLM passa |
| M6 | Passagem, assumir, escalonamento, pausa, desfecho | Fluxos RF10–RF12, RF17, RF19 testados |
| M7 | Painel (gestor/operador), lembrete 9h, relatório, modo demo, direitos do titular | RF14–RF25 |
| M8 | Backup/restore, monitoramento, carga, modo sombra, runbook | RTO simulado; carga dentro da meta |
| M9 | Sincronização iCal e planilha Google (RF26, RF27) | Bloqueio criado no Airbnb aparece em até 15 min; mudança de preço na planilha Google pede aprovação |
| M10 | Assistente de implantação, corretores com plantão e rodízio, painel de resultados, relatório por WhatsApp (RF28–RF30, RF32) | Uma pessoa que não conhece o sistema ativa uma imobiliária fictícia sozinha seguindo o painel |
| M11 | Páginas de produto (RF31), revisão de segurança, conjunto de ouro com 100 casos, `CHANGELOG`, tag `v1.0.0` | Definição de pronto abaixo atendida |

### Definição de pronto da Versão 1.0

A tag `v1.0.0` só é criada quando **tudo** abaixo é verdade:

1. Todos os RF01–RF32 implementados, com testes, e documentados na central de ajuda quando o gestor os usa.
2. Conjunto de ouro de 100 casos aprovado com o LLM real: zero fatos fora da base, 100% dos cards com aviso, passagem correta ≥ 95%.
3. Teste de carga e de falha aprovados; restauração de backup simulada dentro do RTO.
4. Teste de isolamento entre imobiliárias aprovado; varreduras de segurança do CI sem achado alto ou crítico em aberto.
5. Uma imobiliária fictícia ativada do zero pelo assistente de implantação, sem ajuda do operador, em menos de 2 horas.
6. Runbook, termos de uso, política de privacidade e página de status publicados.
7. Contrato e DPA revisados pelo advogado (pendência humana).

---

## 13. Pendências humanas (o código não resolve)

1. Conta no parceiro oficial (BSP) com coexistência e número de teste; verificação da empresa na Meta.
2. Chaves do provedor de IA e de transcrição, com configuração de não uso para treino.
3. Executar a verificação técnica de coexistência: a mensagem enviada pelo app chega ao webhook? (Define se o silêncio automático entra na 1.0 ou fica só o "responda 1" e o botão.)
4. Conta de serviço Google para a planilha sincronizada.
5. Revisão jurídica: contrato SaaS (sem intermediação imobiliária), DPA, termos de uso, política de privacidade, retenção.
6. VPS de produção e de homologação no Brasil, domínio, armazenamento de backup.
