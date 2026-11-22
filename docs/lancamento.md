# Lançamento da Versão 1.0 — definição de pronto

A tag `v1.0.0` só é criada quando **todos** os itens da definição de pronto (SPEC, seção 12) forem
verdade. Situação em 22/11/2026, versão candidata **1.0.0rc1**:

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
