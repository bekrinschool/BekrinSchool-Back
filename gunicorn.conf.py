"""
Gunicorn config for Railway / production (no hardcoded host secrets).

The default Procfile uses inline args (3 workers × 3 threads, gthread). To use this file instead:
  web: gunicorn config.wsgi:application -c gunicorn.conf.py

Env:
  PORT                  — injected by Railway (default 8000)
  WEB_CONCURRENCY       — worker processes (overrides GUNICORN_WORKERS)
  GUNICORN_WORKERS      — default: max(2, 2 * CPU + 1)
  GUNICORN_THREADS      — threads per worker (gthread), default 4
  GUNICORN_TIMEOUT      — seconds, default 120
  GUNICORN_MAX_REQUESTS — recycle workers (optional, reduces leaks)
  GUNICORN_MAX_REQUESTS_JITTER

For ~100+ concurrent *HTTP* clients, raise workers × threads (e.g. 4×8=32) or run more Railway replicas.
Exam submit is handled synchronously in Django — Celery does not absorb submit load unless you offload tasks.
"""
import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

_cpu = multiprocessing.cpu_count() or 1
_default_workers = max(2, min(2 * _cpu + 1, 8))
workers = int(os.environ.get('WEB_CONCURRENCY') or os.environ.get('GUNICORN_WORKERS') or _default_workers)
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
worker_class = 'gthread'

max_requests = os.environ.get('GUNICORN_MAX_REQUESTS')
max_requests_jitter = os.environ.get('GUNICORN_MAX_REQUESTS_JITTER')
if max_requests:
    max_requests = int(max_requests)
    max_requests_jitter = int(max_requests_jitter or '50')
else:
    max_requests = 0
    max_requests_jitter = 0

accesslog = '-'
errorlog = '-'
capture_output = True
