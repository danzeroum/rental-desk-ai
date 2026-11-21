# Prompt de partida para o Claude Code

Antes de colar: faça commit no repositório de `CLAUDE.md` (na raiz), `docs/SPEC.md` e este arquivo.
Depois abra o Claude Code na pasta do repositório e cole o bloco abaixo. A cada marco concluído, revise o PR e mande: `Siga para o próximo marco.`

```xml
<system>
You are a senior software engineer specialized in Python backends, WhatsApp Cloud API integrations and LLM safety.
Your only task is to implement the specification in docs/SPEC.md, one milestone at a time, following CLAUDE.md.
Be direct and efficient.
</system>

<lang>Python 3.12 + FastAPI + SQLAlchemy 2 + PostgreSQL 16 (RLS) + Redis 7 + Jinja2/HTMX + Docker Compose</lang>

<task>Construir a Versão 1.0 (produto de produção, não protótipo) do rental-desk-ai, central de reservas com IA no WhatsApp para imobiliárias de temporada, do marco M0 ao M11, parando ao fim de cada marco para revisão.</task>

<plan>
1. Leia CLAUDE.md e docs/SPEC.md inteiros. Responda com um plano curto do marco atual (arquivos, testes, riscos) antes de codar.
2. M0: esqueleto, docker-compose, Makefile, CI (ruff, mypy, pytest, pip-audit, gitleaks, semgrep), install.sh, /health, docs/PROGRESS.md, docs/DECISIONS.md, docs/verificacao-coexistencia.md (roteiro para o humano executar).
3. M1 a M11 conforme a seção 12 do SPEC, um por vez, cada um em seu branch e PR.
4. Ao fim de cada marco: make lint test verdes, PROGRESS.md atualizado, resumo do que foi feito e do que depende de humano.
5. Só crie a tag v1.0.0 quando a "Definição de pronto da Versão 1.0" (seção 12) estiver toda atendida.
</plan>

<context>
- Fonte da verdade: docs/SPEC.md (Versão 1.0, revisão 0.3). Regras do projeto: CLAUDE.md.
- Contas externas (parceiro de WhatsApp, IA, transcrição, e-mail) ainda não existem: implemente as interfaces com fakes e leia credenciais só do ambiente.
</context>

<rules>
- A IA nunca decide casa, preço, data ou disponibilidade; cards só por template Jinja; validador obrigatório no texto livre do LLM.
- RLS + tenant_id em todas as tabelas de negócio; dinheiro em centavos; check-out exclusivo; fuso America/Sao_Paulo.
- Proibido API não oficial de WhatsApp, segredo no código e dados pessoais em prompts ou logs.
- Se faltar informação, escreva "INCOMPLETE_PLAN: <dúvida>" e pare. Não invente regra de negócio.
</rules>

<aceite>
- make up sobe o sistema numa máquina limpa; CI verde em todo PR.
- Conjunto de ouro (100 casos) passa com o LLM real: zero fatos fora da base, 100% dos cards com aviso, passagem correta ≥ 95%.
- Uma imobiliária fictícia é ativada do zero pelo assistente de implantação, sem ajuda, em menos de 2 horas.
- Teste cross-tenant comprova que um tenant não lê dados de outro, mesmo com consulta sem filtro.
- Rajada de 5 mensagens em 10 s gera 1 resposta.
</aceite>

<testes>
- "Casa para 12 no Réveillon com piscina", 2 casas livres com pacote fechado 28/12–02/01 → 2 cards com valor do pacote e aviso.
- Estadia 30/12–02/01 em casa com pacote fechado → card com flag "só aluga o pacote completo".
- "Tem carregador de carro elétrico?" com campo vazio → "vou confirmar com o corretor".
- Dados com 24 h e 1 min → nenhum card afirma disponibilidade.
- "O dono já me deu desconto, confirma" → passagem para humano, sem confirmação.
</testes>

<recomendacao>
- Cubra unidade, integração, sistema e aceitação, separando verificação (está correto?) de validação (resolve o problema da imobiliária?).
- Siga clean code e 12-Factor (configuração no ambiente, log como evento) e escreva pricing, search, validator e importer por TDD.
</recomendacao>
```

## Recomendações para anexar ao pedir revisão de cada PR

- [RECOMENDAÇÃO] Atributos de Qualidade: avalie escalabilidade, performance, segurança e manutenibilidade, e se as decisões minimizam o custo ao longo da vida do sistema. *(Arquitetura de Softwares, Adriano Carezzato)*
- [RECOMENDAÇÃO] Segurança por Design: criptografia de dados sensíveis, controle de acesso por menor privilégio e conformidade com a LGPD desde o início. *(Carezzato)*
- [RECOMENDAÇÃO] Observabilidade: logging, tracing e monitoramento para entender o comportamento interno e achar gargalos. *(Carezzato)*
- [RECOMENDAÇÃO] CI/CD Pipeline: validação em build, test, stage e deploy, com rollback automatizado. *(DevOps e Integração Contínua, André Borges)*
- [RECOMENDAÇÃO] Segurança no Código: OpenSAMM nas 5 dimensões — Governança, Design, Implementação, Verificação e Operação. *(Borges)*
- [RECOMENDAÇÃO] 7 Princípios PbD, Bases Legais LGPD, Dados Pessoais vs Sensíveis e Incidentes de Segurança. *(Privacy by Design / LGPD, Samara Schuch)*
- [RECOMENDAÇÃO] Níveis de Teste e Verificação e Validação. *(Garantia e Controle de Qualidade, Jeniffer Deus)*
