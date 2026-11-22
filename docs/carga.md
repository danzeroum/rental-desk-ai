# Teste de carga (M8)

Meta (SPEC, seção 11): **200 conversas simultâneas em 10 imobiliárias, sem perda e com p95 ≤ 15 s
depois da janela de debounce** (seção 5).

## Como rodar

```bash
make test-deps-up
make load                      # padrão: 10 imobiliárias, 200 conversas, 40 laços
uv run python -m tests.load.run_load --concurrency 64 --llm-min 1.5 --llm-max 4
```

O script (`tests/load/run_load.py`) usa Postgres e Redis reais, o mesmo pipeline do worker, a fila
com debounce de 6 s e `WORKER_CONCURRENCY` laços no mesmo processo. Cada hóspede manda 1 ou 2
mensagens com intervalo de "digitação"; todos chegam em até 3 s. A IA e o WhatsApp são falsos,
com latência simulada por chamada (a IA faz 1 ou 2 chamadas por resposta). Mede, por conversa, o
tempo entre o fim da janela de debounce e o envio da resposta; perda = conversa sem resposta ou
tarefa na fila de falhas.

## Resultados (22/11/2026, contêiner de desenvolvimento, 1 processo worker)

| Laços (`WORKER_CONCURRENCY`) | Latência da IA por chamada | Respondidas | Mediana | p95 | Resultado |
|---|---|---|---|---|---|
| 12 | 0,8–2,5 s | 200/200 | 22,4 s | 37,1 s | reprovado |
| 40 (padrão) | 0,8–2,5 s | 200/200 | 7,1 s | 10,8 s | aprovado |
| 48 | 0,8–2,5 s | 200/200 | 6,0 s | 8,9 s | aprovado |
| 64 | 0,8–2,5 s | 200/200 | 4,9 s | 6,8 s | aprovado |
| 40 | 1,5–4 s | 200/200 | 12,0 s | 18,3 s | reprovado |
| 64 | 1,5–4 s | 200/200 | 7,8 s | 12,0 s | aprovado |

Nenhuma perda em nenhuma rodada.

## Conclusões

- O gargalo é a espera pela IA, não a CPU nem o banco: cada laço passa quase todo o tempo
  esperando a resposta do modelo. Por isso o worker roda vários laços (D041).
- Padrão `WORKER_CONCURRENCY=40` atende a meta com um modelo rápido. Se o `make golden-live`
  escolher um modelo mais lento (p95 por chamada acima de ~2,5 s), suba para 64.
- Conexões de banco do worker: metade dos laços + 2 (as transações são curtas). O Postgres sobe
  com `max_connections=200`.
- **Pendente com o provedor real:** o limite de requisições por minuto da conta de IA. 200 conversas
  em poucos segundos geram até ~400 chamadas por minuto; confira o limite da conta antes da
  temporada.
