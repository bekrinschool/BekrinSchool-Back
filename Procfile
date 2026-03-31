# Railway / Heroku-style process file. Service root directory: bekrin-back/
# Required: DJANGO_SETTINGS_MODULE=config.settings.prod, DATABASE_URL, DJANGO_SECRET_KEY, ALLOWED_HOSTS, PORT (Railway injects PORT).
# -k gthread is required for --threads (sync workers ignore thread count).
# For ~100–150 users, try 3×3 on Hobby; scale WEB_CONCURRENCY / threads via gunicorn.conf.py by switching web to: gunicorn ... -c gunicorn.conf.py
web: gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --threads 3 --timeout 120
worker: celery -A config worker -l info
