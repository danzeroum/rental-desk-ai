# rental-desk-ai

Central de reservas com IA no WhatsApp para imobiliárias de aluguel por temporada.

- Fonte da verdade do produto: [`docs/SPEC.md`](docs/SPEC.md)
- Regras do projeto: [`CLAUDE.md`](CLAUDE.md)
- Andamento: [`docs/PROGRESS.md`](docs/PROGRESS.md) · Decisões: [`docs/DECISIONS.md`](docs/DECISIONS.md)

## Testar no seu computador (recomendado)

```bash
bash scripts/teste-local.sh   # sobe tudo, cria operador, demonstração e link do kit
```

Guia completo, com o roteiro do que testar em cada tela: [`docs/teste-local.md`](docs/teste-local.md).

## Começo rápido (desenvolvimento)

Requisitos: Docker com Compose v2, `make`, e [uv](https://docs.astral.sh/uv/) para rodar testes fora do Docker.

```bash
make up        # gera .env com segredos aleatórios (se não existir) e sobe tudo
make migrate   # aplica as migrações
curl -k https://localhost/health
```

Testes e qualidade:

```bash
make dev           # instala dependências locais (uv)
make test-deps-up  # Postgres e Redis de teste em portas locais
make lint test
```

## Produção

Numa VPS Ubuntu LTS limpa: `sudo ./deploy/install.sh` (veja `docs/runbook.md`).
