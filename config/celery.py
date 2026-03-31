"""
Celery application for background tasks.

Set DJANGO_SETTINGS_MODULE (e.g. config.settings.prod on Railway) before starting the worker.
Broker/result backend: REDIS_URL or CELERY_BROKER_URL in Django settings.
"""
import os

from celery import Celery

os.environ.setdefault(
    'DJANGO_SETTINGS_MODULE',
    os.environ.get('DJANGO_SETTINGS_MODULE', 'config.settings.dev'),
)

app = Celery('bekrin')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
