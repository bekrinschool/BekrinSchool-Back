"""
Database configuration — PostgreSQL only (no SQLite fallback).
Uses django-environ: .env is read in base.py before this is imported.
"""
from django.core.exceptions import ImproperlyConfigured

# env is imported from base after BASE_DIR; we need env in scope.
# This module is imported from base.py, so we receive env as dependency
# or we read from os.environ (already loaded by base).
def get_database_config(env):
    """
    Return Django DATABASES['default'] for PostgreSQL only.
    - If DATABASE_URL is set and non-empty: use it.
    - Else if DB_NAME + DB_USER (and optionally DB_PASSWORD, DB_HOST, DB_PORT) are set: build config.
    - Else: raise ImproperlyConfigured (no silent SQLite fallback).
    """
    raw_url = (env.str('DATABASE_URL', default='') or '').strip()
    if raw_url:
        cfg = env.db_url_config(raw_url)
        cfg.setdefault('CONN_MAX_AGE', env.int('DB_CONN_MAX_AGE', default=0))
        return cfg

    name = (env.str('DB_NAME', default='') or '').strip()
    user = (env.str('DB_USER', default='') or '').strip()
    password = env.str('DB_PASSWORD', default='')
    host = env.str('DB_HOST', default='localhost')
    port = env.str('DB_PORT', default='5432')  # Use 5433 in .env if Postgres runs on non-default port

    if not name or not user:
        raise ImproperlyConfigured(
            'PostgreSQL is required. Set DATABASE_URL (e.g. postgresql://user:pass@host:5432/dbname) '
            'or set DB_NAME and DB_USER (and optionally DB_PASSWORD, DB_HOST, DB_PORT) in .env. '
            'SQLite fallback is disabled.'
        )

    conn_max_age = env.int('DB_CONN_MAX_AGE', default=0)
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': name,
        'USER': user,
        'PASSWORD': password,
        'HOST': host,
        'PORT': str(port),
        'CONN_MAX_AGE': conn_max_age,
        'OPTIONS': {},
    }
