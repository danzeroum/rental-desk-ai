"""Processo `scheduler`: escalonamento de passagens (lembrete e relatório entram no M7)."""

from app.core.service import main
from app.scheduler.app import run_scheduler

if __name__ == "__main__":
    main("scheduler", background=run_scheduler)
