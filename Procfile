# Process types for Heroku-style hosts (e.g. Railway can also read Procfile).
# Service root: bekrin-back/
#
# Required env: DJANGO_SETTINGS_MODULE=config.settings.prod, DATABASE_URL, DJANGO_SECRET_KEY,
# ALLOWED_HOSTS, CORS_ALLOWED_ORIGINS, CSRF_TRUSTED_ORIGINS, REDIS_URL (for web cache + worker).
# Railway: prefer railway.toml for build/start/release; this file documents the worker + web split.
#
# Web: gthread backend is required when using --threads.
web: gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --threads 3 --timeout 120

# Celery worker (separate dyno/service; do not run HTTP health checks on this process).
worker: celery -A config worker -l info

# Optional Heroku release phase (uncomment if your platform runs Procfile "release"):
# release: sh -c 'export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.prod}" && python manage.py migrate --noinput && python manage.py collectstatic --noinput'
