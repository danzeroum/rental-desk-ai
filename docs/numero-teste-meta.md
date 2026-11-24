# Testar com o número de teste da Meta (sem parceiro)

O número de teste gratuito da Cloud API da Meta permite conversar com o assistente pelo seu WhatsApp de
verdade, antes de o parceiro (BSP) aprovar o número da imobiliária. Ele aceita até 5 destinatários
cadastrados e não precisa de empresa verificada para **receber** mensagens.

## 1. Na Meta (uma vez)

1. Crie um app em <https://developers.facebook.com/apps/creation/> com o caso de uso
   **"Conectar-se com clientes pelo WhatsApp"** e o seu portfólio empresarial.
2. Em **WhatsApp → Etapa 1. Experimente**: "Reivindicar número de teste", cadastre o seu celular como
   destinatário e anote o **Phone Number ID**.
3. **Token:** o "Gerar token" da tela vale 24 h. Para não trocar todo dia, crie um usuário do sistema em
   <https://business.facebook.com/settings/system-users> (Administrador, com o app e a conta do
   WhatsApp atribuídos) e gere um token sem validade com `whatsapp_business_messaging` e
   `whatsapp_business_management`.
4. **Chave secreta do app:** Configurações do app → Básico → "Mostrar".

Nunca cole o token nem a chave em chat, e-mail ou arquivo do repositório.

## 2. No servidor (já instalado)

```bash
cd ~/rental-desk-ai
```

```bash
bash scripts/numero-teste-meta.sh <phone_number_id>
```

O script pede a chave e o token sem mostrar na tela, liga `WHATSAPP_PROVIDER=cloud_api`, reinicia os
serviços, conecta o número à **Imobiliária Demo Riviera** e confere o webhook do mesmo jeito que a
Meta confere. No fim, ele imprime a URL e o token de verificação.

## 3. Webhook na Meta

WhatsApp → **Configuração** → Webhook → Editar:

- URL de callback: `https://<seu-domínio>/webhooks/whatsapp`
- Token de verificação: o que o script imprimiu
- **Verificar e salvar** e assine o campo **messages**

## 4. Testar

Do seu WhatsApp, mande para o número de teste: `Casa para 8 pessoas de 28/12 a 02/01 com piscina`.
Veja no painel: Operador → **Entrar como suporte** → Demo Riviera.

## Se a Meta bloquear os envios (erro 130497)

Contas novas ou sem empresa verificada podem receber o erro **130497** ("Business account is
restricted from messaging users in this country"): a Meta aceita o envio, mas não entrega. A mensagem
do hóspede chega ao sistema; só a resposta não chega ao celular.

Enquanto isso, use o **modo sombra** para ver as respostas:

1. Painel da Demo Riviera → **Situação** → "voltar ao modo sombra".
2. Mande mensagens do seu WhatsApp normalmente.
3. Em **Situação**, a lista "Últimas respostas que o assistente daria" mostra cada resposta: cartões,
   valores e passagens. Nada é enviado e nenhum corretor é avisado.

Para liberar o envio: forma de pagamento na conta de mensagens, **verificação da empresa** (Configurações
do portfólio → Informações da empresa) e, se continuar, um chamado em
<https://business.facebook.com/direct-support> citando o código 130497, o WABA e o Phone Number ID.

Rodar `make seed-demo` de novo volta a Demo Riviera para "atendendo".
