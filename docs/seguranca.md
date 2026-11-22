# Revisão de segurança (M11, antes da Versão 1.0)

Revisão do código e da instalação contra as regras do CLAUDE.md e o SPEC (seções 5 e 9). Tudo o que
dá para conferir automaticamente virou teste e roda no CI.

## Conferido e coberto por teste

| Área | Como está | Onde é testado |
|---|---|---|
| Isolamento entre imobiliárias | RLS forçado em toda tabela de negócio (falha fechada sem imobiliária na sessão); papel da aplicação sem BYPASSRLS; o operador só lê a lista de imobiliárias por uma política própria | `tests/integration/test_tenant_isolation.py` (percorre todas as tabelas) |
| Login | senha argon2id + autenticador obrigatório; bloqueio por senha (5), por IP (30/10 min) e por código (5); token trocado ao concluir o autenticador; sessão de 8 h sem uso e 12 h no máximo | `test_panel_auth.py` |
| Rotas privadas | nenhuma rota do painel ou do operador abre sem login | `test_public_and_security.py` (percorre todas as rotas) |
| CSRF | todo POST exige o token de duplo envio e confere a origem | `test_public_and_security.py`, `test_panel_auth.py` |
| Cabeçalhos | CSP sem `unsafe-inline`, `frame-ancestors 'none'`, HSTS no Caddy, cookies `Secure`/`HttpOnly`/`SameSite` | `test_panel_auth.py`, navegador real (sem erro de CSP) |
| Webhook | assinatura `X-Hub-Signature-256` obrigatória; limite de tamanho | `test_webhook_flow.py` |
| Endereços externos (fotos, iCal) | só http(s) público, porta padrão, conferido a cada redirecionamento; limite de tamanho e tempo | `test_http_fetch.py`, `test_link_checker.py` |
| Planilha | até 5 MB; leitura do .xlsx com checagem do contêiner (zip bomb) | `tests/unit/importer` |
| Dados pessoais | máscara nos logs e no texto enviado à IA; `audit_log` sem telefone nem texto do hóspede; exportar e apagar por telefone | `test_masking.py`, `test_subject_rights.py`, `tests/golden` |
| Segredos | só no `.env`; tokens de terceiros (WhatsApp, iCal) cifrados no banco; backup cifrado | `test_crypto.py`, gitleaks no CI |
| Dependências e código | pip-audit, bandit, semgrep, gitleaks no CI, sem achado em aberto | job "Segurança" do CI |

## Corrigido nesta revisão

- A sessão se renovava sem limite enquanto houvesse uso: agora termina em 12 h.
- O token da etapa da senha continuava valendo depois do autenticador: agora é trocado.

## Achados de marcos anteriores (já corrigidos, para registro)

- Telefone do hóspede ia para o `audit_log` (imutável) na confirmação ao corretor (M7).
- Fila de falhas sem limite; mensagem perdida com Redis fora entre gravar e agendar (M8).
- Consultas montadas com f-string no scheduler (bandit B608), trocadas por consultas fixas (M9).

## Riscos aceitos

- **DNS rebinding** na leitura de endereços externos: a checagem do IP e a conexão são dois passos.
  Aceito porque o conteúdo lido vira só datas e links, e a resposta não volta ao usuário.
- **`--forwarded-allow-ips *` no uvicorn:** seguro porque só o Caddy é exposto e ele define o
  `X-Forwarded-For`; se o app for publicado direto, trocar pelo IP do Caddy.

## Pendente (humano)

- Teste de invasão por terceiro antes do lançamento comercial.
- Revisão jurídica do aviso de privacidade, dos termos de uso, da política de privacidade e do DPA.
