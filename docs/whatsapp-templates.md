# Templates do WhatsApp (mensagens ativas)

Mensagens enviadas pelo número da imobiliária **fora da janela de 24 h** (avisos ao
corretor e ao gestor) só saem por template aprovado pela Meta. **Pendência humana:**
cadastrar os templates abaixo na conta do parceiro (BSP) ou no WhatsApp Manager, na
categoria **Utilidade**, idioma **Português (BR) — `pt_BR`**, com o nome exato.

O código preenche os parâmetros `{{n}}` (sem quebras de linha, até 1.000 caracteres).
A fonte é `app/domain/notices.py`; um teste garante que cada `{{n}}` tem parâmetro.
Avisos só vão para quem tem consentimento registrado (`consent`, finalidade
`handoff_alert`).

## `passagem_corretor`

Para o corretor da vez (rodízio), quando o assistente passa a conversa.

> Nova passagem da {{1}}: {{2}}. Hóspede: {{3}}. Pedido: {{4}}. Responda 1 para assumir a conversa.

| Parâmetro | Conteúdo | Exemplo |
|---|---|---|
| {{1}} | nome da imobiliária | Imobiliária Demo Riviera |
| {{2}} | motivo | escolheu uma casa |
| {{3}} | telefone do hóspede | +5511955554444 |
| {{4}} | resumo do pedido | 12 pessoas · 28/12/2026 a 02/01/2027 · piscina · escolheu CASA06 |

## `passagem_copia_gestor`

Cópia para o gestor (RF29: "gestor sempre em cópia").

> Cópia para o gestor da {{1}}: passagem ({{2}}) do hóspede {{3}} enviada para {{4}}. Pedido: {{5}}.

| Parâmetro | Conteúdo |
|---|---|
| {{1}} | nome da imobiliária |
| {{2}} | motivo |
| {{3}} | telefone do hóspede |
| {{4}} | nome do corretor avisado |
| {{5}} | resumo do pedido |

## `alerta_passagem`

Para o gestor aos 15 minutos comerciais sem ninguém assumir (RF12). O operador recebe o
mesmo alerta por e-mail.

> Atenção, {{1}}: a passagem ({{2}}) do hóspede {{3}} está há {{4}} minutos sem corretor. Assuma pelo painel ou peça a um corretor para responder 1 ao aviso.

| Parâmetro | Conteúdo |
|---|---|
| {{1}} | nome da imobiliária |
| {{2}} | motivo |
| {{3}} | telefone do hóspede |
| {{4}} | minutos comerciais |

## Respostas dentro da janela (sem template)

A confirmação ao corretor depois do "1" e as mensagens ao hóspede são respostas dentro
da janela de 24 h aberta pela mensagem da própria pessoa; não precisam de template.

Os templates do lembrete das 9h e do relatório semanal entram no M7.
