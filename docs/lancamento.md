# Lançamento da Versão 1.0 — definição de pronto

A tag `v1.0.0` só é criada quando **todos** os itens da definição de pronto (SPEC, seção 12) forem
verdade. Situação em 22/11/2026, versão candidata **1.0.0rc1** (tag `v1.0.0-rc.1` após o merge do PR):

| # | Item | Situação | O que falta |
|---|---|---|---|
| 1 | RF01–RF32 implementados, com testes, e documentados na central de ajuda | ✅ código e testes; central de ajuda em `/ajuda` | — |
| 2 | Conjunto de ouro de 100 casos aprovado com o **LLM real** (zero fatos fora da base, 100% dos cards com aviso, passagem correta ≥ 95%) | ⏳ 100 casos (65 + 35) aprovados com o FakeLLM | **Humano:** chave da IA e `make golden-live`; escolher o modelo pela tabela gerada |
| 3 | Teste de carga e de falha aprovados; restauração de backup dentro do RTO | ✅ carga (`docs/carga.md`), falhas e restauração testadas no CI | **Humano:** `make restore-drill` na VPS de produção e registro do tempo |
| 4 | Isolamento entre imobiliárias; varreduras de segurança sem achado alto ou crítico | ✅ (`docs/seguranca.md`) | Teste de invasão por terceiro (recomendado) |
| 5 | Imobiliária fictícia ativada do zero pelo assistente de implantação, sem ajuda, em menos de 2 h | ⏳ percurso completo automatizado (`tests/system/test_onboarding_walkthrough.py`) | **Humano:** uma pessoa de fora, cronometrada, numa VPS de homologação |
| 6 | Runbook, termos de uso, política de privacidade e página de status publicados | ⏳ publicados em `/termos`, `/politica-de-privacidade`, `/status`; runbook em `docs/runbook.md` | **Humano:** revisão jurídica dos textos-base |
| 7 | Contrato e DPA revisados pelo advogado | ⏳ | **Humano** |

## Para criar a tag

1. Feche os itens ⏳ acima.
2. Em `pyproject.toml` e `app/__init__.py`, troque `1.0.0rc1` por `1.0.0`; no `CHANGELOG.md`, renomeie
   a seção `1.0.0rc1` para `1.0.0` com a data.
3. `git tag -a v1.0.0 -m "Versão 1.0"` e `git push origin v1.0.0`.

## Ordem sugerida das suas pendências

Começa pelo que demora mais para voltar (aprovações externas) e pelo que destrava os itens seguintes.
O M12 (tenant próprio, demos por segmento) só começa depois da `v1.0.0`.

1. **Conferir e mesclar o PR do candidato** (danzeroum/rental-desk-ai#1). Depois do merge, a tag
   `v1.0.0-rc.1` é criada em `main` (autorizada).
2. **Abrir já os pedidos com espera longa, em paralelo:**
   - conta no parceiro (BSP) e envio dos 5 templates de `docs/whatsapp-templates.md`;
   - advogado: aviso de privacidade, termos, política de privacidade, contrato e DPA (itens 6 e 7),
     incluindo as cláusulas de transferência internacional (LGPD art. 33; cláusulas-padrão da ANPD)
     com a Meta, o provedor de IA e o de transcrição (D050).
3. **Chave da IA** (`LLM_API_KEY`, `LLM_MODEL`) → `make golden-live` → escolher o modelo pela tabela
   (item 2). A mesma chave deixa o kit de demonstração com a IA real.
4. **VPS de homologação + domínio** → `sudo ./deploy/install.sh` com `ENV=staging` → `make seed-demo`
   → criar os links de demonstração em `/operador/demo`. **A partir daqui dá para vender** com o kit
   (seção abaixo), mesmo sem o WhatsApp aprovado.
5. **Teste de implantação com uma pessoa de fora**, cronometrado, na homologação (item 5).
6. **Chaves restantes:** SMTP, conta de serviço do Google, armazenamento S3 dos backups, transcrição.
7. **Número aprovado no parceiro** → cadastro em coexistência → verificação de coexistência
   (`docs/verificacao-coexistencia.md`) → decidir `WHATSAPP_ECHO_MUTE`.
8. **VPS de produção** → monitor externo do `/health` → `make restore-drill` com o tempo registrado
   (item 3) → limite de requisições da conta de IA.
9. **Validação com uma imobiliária real:** casos de cálculo feitos à mão; primeira imobiliária em modo
   sombra.
10. Itens 1 a 7 verdes → **tag `v1.0.0`** (passos acima) → M12.

## Kit de demonstração para vendas

Funciona **sem o WhatsApp oficial**: o visitante conversa numa tela com aparência de WhatsApp, e as
respostas saem do mesmo pipeline do produto (debounce, extração, motor, cartões, passagem). Os números
das imobiliárias de demonstração começam com `demo-` e nunca saem para a Meta (D045).

- **Empresas fictícias**, marcadas como demonstração: **Imobiliária Maré Alta** (8 casas) e **Pousada
  Recanto da Enseada** (6 quartos, no mesmo domínio de casas, D046). `make seed-demo` cria ou renova as
  duas, junto com a Demo Riviera do painel.
- **Duas telas lado a lado** (empilhadas no celular): a conversa do hóspede e o WhatsApp do corretor,
  com o aviso "responda 1" e o botão **Responder 1**, que assume a conversa do mesmo jeito que no
  WhatsApp.
- **IA:** sem chave, usa o FakeLLM (entende os pedidos das sugestões e parecidos); com `LLM_API_KEY`
  configurada, usa a IA real. Nada mais muda.
- **Acesso por link com token**, sem login de painel. O operador cria em **Operador → Demonstração**
  (`/operador/demo`), dá um rótulo (sem telefone nem e-mail), copia o link (ele aparece uma vez só) e
  pode **revogar** a qualquer momento: o link e as visitas abertas param na hora. Cada visita dura 4 h,
  com até 80 mensagens.
- **Nenhum dado real:** o visitante recebe um telefone fictício (+55 009…), e as empresas, casas, preços
  e fotos são inventados. **Todo dia às 3h** o scheduler apaga contatos, conversas e telas de todas as
  imobiliárias de demonstração.

Roteiro de 5 minutos para a reunião:

1. Abra o link no celular do cliente e escolha a Maré Alta.
2. Toque em "Casa para 8 pessoas de 28/12 a 02/01 com piscina": cartões com pacote Réveillon, valores e
   o aviso "sujeito à confirmação do corretor". Os valores vêm da planilha, não da IA.
3. "Quero a primeira": role até o celular do corretor e mostre o aviso que chega no WhatsApp dele.
4. Toque em **Responder 1**: o corretor assume e o assistente fica em silêncio nessa conversa.
5. "Recomeçar" e repita com a Pousada, se o cliente for pousada.

## Pendências humanas consolidadas

- **WhatsApp:** conta no parceiro (BSP), cadastro do número em coexistência, aprovação dos 5 templates de
  `docs/whatsapp-templates.md`, verificação de coexistência (`docs/verificacao-coexistencia.md`) para
  decidir `WHATSAPP_ECHO_MUTE`.
- **Chaves:** IA (`LLM_API_KEY`), transcrição (`TRANSCRIBER_API_KEY`), SMTP, conta de serviço do Google,
  armazenamento S3 dos backups.
- **Operação:** VPS de produção e de homologação, monitor externo do `/health`, limite de requisições da
  conta de IA, exercício de restauração.
- **Jurídico:** aviso de privacidade, termos, política de privacidade, contrato e DPA.
- **Validação:** casos de cálculo feitos à mão por uma imobiliária real; teste de implantação com pessoa
  de fora.
