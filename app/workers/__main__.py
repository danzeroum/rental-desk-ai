"""Processo `worker`: consome a fila de conversas (debounce, transcrição, resposta)."""

from app.core.service import main
from app.workers.app import run_worker

if __name__ == "__main__":
    main("worker", background=run_worker)
