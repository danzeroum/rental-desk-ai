# AGENTS.md — rental-desk-ai

Instruções para agentes de código (OpenCode, Codex e similares). As regras completas do projeto
estão em `CLAUDE.md` e valem para qualquer agente; a especificação é `docs/SPEC.md`.

## Testar no computador do dono do projeto

O dono quer ver o sistema rodando e entender o que foi construído. Siga `docs/teste-local.md`:

1. Na raiz do repositório: `bash scripts/teste-local.sh` (acrescente `--testes` se o `uv` estiver
   instalado). O script é idempotente: pode rodar de novo.
2. Se um passo falhar, leia `teste-local.log`, explique a causa em português simples e proponha a
   correção. Não altere código do produto para "fazer funcionar" sem perguntar.
3. Ao terminar, mostre o conteúdo de `ACESSO-LOCAL.md` e conduza o roteiro da seção 4 do guia,
   explicando cada tela com as referências de código indicadas.

## Nunca

- Commitar `.env`, `ACESSO-LOCAL.md` ou `teste-local.log` (têm segredos e links de acesso).
- Desativar RLS, a validação de assinatura do webhook ou o validador de respostas.
- Usar WhatsApp Web, QR code, Baileys, Evolution, WAHA ou similares (só a API oficial).
- Mandar dados pessoais (nome, telefone) para a IA ou para os logs.
