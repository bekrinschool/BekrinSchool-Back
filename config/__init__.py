# Django config package — load Celery app with Django (worker uses config.celery)
from .celery import app as celery_app

__all__ = ('celery_app',)

