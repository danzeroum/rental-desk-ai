# Verificação técnica de coexistência (≈ 1 hora)

**Quem executa:** uma pessoa do time (pendência humana nº 3 do SPEC, seção 13).
**Quando:** assim que houver conta no parceiro oficial (BSP) com coexistência e um número de teste.
**Para quê:** responder a uma pergunta que decide o RF11:

> Quando o corretor responde ao hóspede **pelo app WhatsApp Business** (número em coexistência),
> essa mensagem chega ao nosso webhook?

- **Sim, de forma confiável** → o "silêncio automático por eco" entra na 1.0: o assistente fica mudo
  na conversa assim que o corretor escreve pelo app, sem precisar responder "1" ou clicar em "assumir".
- **Não, ou não confiável** → ficam só os caminhos que não dependem da Meta: o corretor responde "1"
  ao aviso de passagem ou clica em "assumir" no painel (já previstos no RF11).

Isto **não** é prova de conceito do produto. É a conferência de um comportamento da Meta que não dá
para supor. Não muda nenhum outro requisito.

---

## 1. O que preparar (antes de começar o relógio)

| Item | Detalhe |
|---|---|
| Número de teste | Um número **de teste** já ativo no app **WhatsApp Business** de um celular ("celular da imobiliária"). Nunca use o número de um cliente nesta etapa. |
| Conta no parceiro (BSP) | Com coexistência habilitada. Anote o nome do parceiro e o plano. |
| Segundo celular | Com WhatsApp comum, faz o papel do **hóspede**. |
| Um computador | Para ver os eventos do webhook. |
| Onde ver os eventos | **Opção A (mais simples):** o visualizador de logs de webhook do próprio parceiro. **Opção B:** o capturador deste repositório (`scripts/capture_webhook.py`, abaixo). |

### Opção B: capturador do repositório

Precisa de um endereço **HTTPS público** apontando para a porta local. Use a VPS de homologação
(com o Caddy apontando para a porta 8080) ou um túnel HTTPS da sua preferência.

```bash
VERIFY_TOKEN=escolha-um-token APP_SECRET=segredo-do-app \
  python3 scripts/capture_webhook.py --port 8080 --out captura.jsonl
```

- `VERIFY_TOKEN`: o mesmo texto que você cadastrar como "verify token" no parceiro/Meta.
- `APP_SECRET`: o segredo do app, se o parceiro repassar a assinatura `X-Hub-Signature-256`.
  Sem ele o capturador funciona, mas não confere a assinatura.
- Cada evento vira uma linha em `captura.jsonl`, com o horário de chegada e os campos recebidos.
- **O arquivo contém telefones.** Apague-o ao final (passo 6), depois de copiar os exemplos
  anonimizados para o relatório.

---

## 2. Conectar o número em coexistência (≈ 15 min)

1. No painel do parceiro, inicie o cadastro (Embedded Signup) escolhendo **"conectar um número que já
   usa o app WhatsApp Business"**. Siga o fluxo no celular da imobiliária (leitura do QR **pelo próprio
   app WhatsApp Business**, dentro do fluxo oficial da Meta. Nada de WhatsApp Web não oficial).
2. Aceite o compartilhamento do histórico quando o fluxo oferecer.
3. Configure o webhook (URL e verify token) e **assine estes campos**, se o parceiro deixar escolher:
   - `messages` (mensagens dos hóspedes e status);
   - `smb_message_echoes` (mensagens enviadas pelo app WhatsApp Business: é este o "eco" que queremos);
   - opcional: `smb_app_state_sync` e `history`.
   Se o parceiro usar outros nomes, anote quais são.
4. Anote a **URL base da API** e o **cabeçalho de autenticação** que o parceiro usa para enviar mensagens.
   O `WhatsAppGateway` (M4) precisa disso.

## 3. Testes (≈ 30 min)

Faça cada teste e preencha a tabela do passo 5. Para medir o atraso, anote a hora do envio no
celular e compare com o horário de chegada no log.

| # | Ação | O que observar |
|---|---|---|
| T1 | Hóspede manda "oi, teste 1" para o número | Chega evento `messages` com o texto? O celular da imobiliária **também** mostra a mensagem no app? |
| T2 | Corretor responde **pelo app no celular principal**: "resposta teste 2" | Chega algum evento? Em qual campo (`smb_message_echoes`?) Atraso em segundos? |
| T3 | Repita T2 mais **9 vezes** (10 no total), espaçadas | Quantas das 10 chegaram? Pior atraso? |
| T4 | Corretor responde por um **aparelho vinculado oficial** (WhatsApp Business no computador ou no tablet, vinculado ao celular) | Chega o eco? Igual ao T2? |
| T5 | Corretor manda um **áudio** e uma **foto** pelo app | Chegam ecos de mídia? Com que tipo? |
| T6 | Envie uma mensagem **pela API** (ferramenta de teste do parceiro) para o hóspede | A mensagem aparece no app do celular da imobiliária, para o corretor ver o histórico? |
| T7 | Corretor **inicia** conversa pelo app com um contato que nunca escreveu | Chega eco? (caso raro, só para registro) |

### O que conferir em cada eco recebido (T2–T5, T7)

- Dá para saber **de qual conversa** é: o telefone do hóspede aparece no evento (ex.: campo `to`)?
- Dá para distinguir eco de mensagem do hóspede só olhando o evento (campo diferente ou marcador)?
- Tem um ID de mensagem único (para não processar duas vezes)?
- A assinatura `X-Hub-Signature-256` veio e é válida? (o capturador mostra `assinatura=True`)

## 4. Critério de decisão

O silêncio automático por eco **entra na 1.0** se **tudo** abaixo for verdade:

1. T3: **10 de 10** ecos chegaram;
2. T3: pior atraso **≤ 10 s** (o debounce do assistente é de 6 s; um eco mais lento que isso deixaria o
   assistente responder por cima do corretor);
3. Todo eco identifica a conversa (telefone do hóspede) e é distinguível de mensagem do hóspede;
4. T1: a mensagem do hóspede continua aparecendo no app (o corretor não perde nada).

Se qualquer item falhar, o silêncio por eco **fica fora da 1.0**, e o RF11 segue só com "responda 1" e
o botão "assumir" do painel. T4, T5, T6 e T7 não decidem: ajustam detalhes do M6.

## 5. Relatório (≈ 10 min)

Copie para `docs/DECISIONS.md` (decisão "Coexistência: silêncio automático por eco") e preencha:

```markdown
### Resultado da verificação de coexistência (data: AAAA-MM-DD, responsável: ...)

- Parceiro (BSP): ...                 Plano: ...
- URL base da API: ...                Cabeçalho de autenticação: ...
- Campos de webhook assinados: ...

| Teste | Resultado | Atraso (s) | Observação |
|---|---|---|---|
| T1 | | | |
| T2 | | | |
| T3 | __/10 | pior: | |
| T4 | | | |
| T5 | | | |
| T6 | | | |
| T7 | | | |

Critério 1: sim/não · 2: sim/não · 3: sim/não · 4: sim/não
**Decisão:** silêncio automático por eco ENTRA / NÃO ENTRA na 1.0.

Exemplo de evento de eco (telefones trocados por 5500000000000):
(cole aqui um evento de `smb_message_echoes` anonimizado)
```

## 6. Limpeza (≈ 5 min)

- Apague `captura.jsonl` e qualquer cópia com telefones reais.
- Se usou túnel, encerre-o e remova a URL do webhook no parceiro (ou troque pela da homologação).
- Guarde o número de teste conectado: ele será usado no modo demonstração (RF25) e nos testes.
