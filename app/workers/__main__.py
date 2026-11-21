"""Processo `worker`: consome a fila de mensagens (pipeline a partir do M4)."""

from app.core.service import main

if __name__ == "__main__":
    main("worker")
