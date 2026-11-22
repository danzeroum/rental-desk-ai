# Como preencher a planilha modelo

Texto-fonte da central de ajuda (RF31). A planilha tem 4 abas. Baixe o modelo no painel
(ou `make template`): ele já vem com listas de Sim/Não, formato de data e os códigos das
casas para escolher.

## Imoveis

Uma linha por casa. O **codigo** (ex.: CASA12) liga esta aba às outras e não pode se repetir.
Atributos (piscina, aceita_pet, wifi...) aceitam **Sim**, **Não** ou **vazio**. Vazio quer dizer
"não sabemos": o assistente nunca responde "não" para um campo vazio, ele diz ao hóspede que vai
confirmar com o corretor.

## Tarifas

Uma linha por período de preço, com a **primeira noite** e a **última noite** do período.
As duas datas estão incluídas.

| Situação | primeira_noite | ultima_noite |
|---|---|---|
| Dezembro inteiro, antes do Natal | 01/12 | 23/12 |
| Réveillon: entrada 28/12, saída 02/01 | 28/12 | 01/01 |
| Uma noite especial | 31/12 | 31/12 |

- Os períodos da mesma casa **não podem se sobrepor** (a planilha é recusada, com a linha indicada).
- Se faltar alguma noite entre dois períodos, aparece um aviso: a casa não será oferecida
  para quem quiser essas noites.
- **Pacote** (ex.: Réveillon): preencha `pacote_nome` e `pacote_valor` (valor do pacote inteiro).
  `pacote_fechado = Sim` quer dizer que a casa só aluga o pacote completo; `Não` aceita parte do
  período, cobrando a diária.
- `minimo_noites` vazio = 1. `taxa_limpeza` vazia = 0. Valores em reais (ex.: 1500,00).

## Bloqueios

Uma linha por ocupação, com a **entrada** e a **saída** do hóspede, como no check-in e no
check-out. No dia da saída a casa já fica livre para uma nova entrada.

| Situação | entrada | saida |
|---|---|---|
| Hóspede chega 28/12 e vai embora 02/01 | 28/12 | 02/01 |

Bloqueios também podem ser atualizados pelo painel, pela resposta ao lembrete das 9h
("CASA12 28/12-02/01", com as mesmas datas de entrada e saída) e pelos calendários iCal.

## Regras

Chave e valor curtos (check-in, check-out, caução, pagamento, cancelamento). O assistente só
repassa o que estiver aqui.

## Depois de enviar

O painel lista os **erros** (precisam ser corrigidos; a planilha não entra) e os **avisos**
(não impedem), com aba, linha e coluna. Nada vai ao ar antes da sua aprovação.
