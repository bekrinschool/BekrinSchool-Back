"""Celery tasks (autodiscovered via config.celery.app.autodiscover_tasks)."""
from celery import shared_task


@shared_task(name='core.ping')
def ping() -> str:
    """Smoke test: `celery -A config call core.ping` from a shell with broker configured."""
    return 'pong'
