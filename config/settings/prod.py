"""
Production settings
"""
import warnings

from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False

# Fail fast in production if the dev placeholder key is still in use (no secret in repo).
if SECRET_KEY == 'django-insecure-dev-key-change-in-production':
    raise ImproperlyConfigured(
        'Set DJANGO_SECRET_KEY in the environment (Railway Variables). '
        'Do not use the development default in production.'
    )

# Hosts: empty list breaks all requests; django-environ can yield [] from a bad value.
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        'ALLOWED_HOSTS must be non-empty in production. '
        'Set a comma-separated list (e.g. your-service.up.railway.app).'
    )

# DB connection reuse (base/database defaults CONN_MAX_AGE to 0 when unset). Prod default 60s unless DB_CONN_MAX_AGE is set (including 0).
DATABASES['default']['CONN_MAX_AGE'] = env.int('DB_CONN_MAX_AGE', default=60)

_redis_prod = (env.str('REDIS_URL', default='') or '').strip()
_celery_broker_prod = (env.str('CELERY_BROKER_URL', default='') or '').strip()
if not _redis_prod and not _celery_broker_prod:
    warnings.warn(
        'REDIS_URL and CELERY_BROKER_URL are unset: Django cache falls back to LocMem; '
        'Celery defaults to redis://127.0.0.1:6379/0 and will fail on Railway unless you set REDIS_URL '
        '(or CELERY_BROKER_URL / CELERY_RESULT_BACKEND) on web and worker.',
        RuntimeWarning,
        stacklevel=1,
    )

# Production security settings (override via env on PaaS / edge cases)
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)
SESSION_COOKIE_SECURE = env.bool('SESSION_COOKIE_SECURE', default=True)
CSRF_COOKIE_SECURE = env.bool('CSRF_COOKIE_SECURE', default=True)
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = env.str('X_FRAME_OPTIONS', default='DENY')

# HSTS: default 0 until you confirm HTTPS-only everywhere (misconfigured HSTS is hard to undo).
# After verification, set e.g. SECURE_HSTS_SECONDS=31536000 (1 year) in Railway.
SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False)
SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=False)

# OpenAPI: operationId collisions and APIView schema guesses — docs only; not runtime or Railway health.
SILENCED_SYSTEM_CHECKS = [
    'drf_spectacular.W001',
    'drf_spectacular.W002',
]

# Railway / reverse proxies terminate TLS; Django must trust X-Forwarded-Proto
if env.bool('USE_TLS_PROXY_HEADERS', default=True):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Split frontend (Next.js) on another origin → must list HTTPS origins or browsers block API calls / CSRF.
_cors = [str(o).strip().lower() for o in (CORS_ALLOWED_ORIGINS or []) if str(o).strip()]
_csrf = [str(o).strip().lower() for o in (CSRF_TRUSTED_ORIGINS or []) if str(o).strip()]
def _only_loopback(origins: list[str]) -> bool:
    return bool(origins) and all(
        o.startswith('http://localhost') or o.startswith('http://127.0.0.1') for o in origins
    )
if _only_loopback(_cors) or _only_loopback(_csrf):
    warnings.warn(
        'CORS_ALLOWED_ORIGINS / CSRF_TRUSTED_ORIGINS look localhost-only. '
        'Add your production frontend URL(s), e.g. https://your-app.up.railway.app',
        RuntimeWarning,
        stacklevel=1,
    )

# DATABASES: CONN_MAX_AGE applied above; source is base → database.get_database_config (DATABASE_URL or DB_*).
